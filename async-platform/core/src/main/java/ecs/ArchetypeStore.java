package ecs;

import java.util.Arrays;

/**
 * Хранилище ОДНОГО архетипа: непрерывные SoA-колонки только для компонентов своей маски.
 * Разреженность живёт здесь — архетип не платит за чужие компоненты (в фикс-колонках World
 * каждая новая подсистема добавляла колонку ВСЕМ энтити сразу).
 *
 * Колонки НЕПРЕРЫВНЫ, а не список чанков — осознанно. Системы читают соседей по индексу внутри
 * своего архетипа (MobSense: 16 чтений на моба, RedstonePropagate: 4 на клетку); чанковая
 * раскладка добавила бы им div/mod + 2 индирекции на КАЖДОЕ такое чтение и утопила бы срезы 4-5.
 * Чанк в этой модели — диапазон [lo,hi) для нарезки параллельных задач, а НЕ единица хранения.
 *
 * Индексация: col[row * arity + lane] — та же форма, что прежний inv[e*SLOTS+slot].
 */
public final class ArchetypeStore {
    public final long mask;
    private final ComponentRegistry reg;

    // Индексируются id компонента; null там, где компонента нет в маске. Прямой доступ без хеша.
    private final int[][] intCols;
    private final long[][] longCols;
    private final double[][] doubleCols;

    private int[] rowToEntity;
    private int size;
    private int capacity;

    public ArchetypeStore(ComponentRegistry reg, long mask, int initialCapacity) {
        this.reg = reg;
        this.mask = mask;
        this.capacity = Math.max(1, initialCapacity);
        int n = reg.count();
        intCols = new int[n][];
        longCols = new long[n][];
        doubleCols = new double[n][];
        for (int c = 0; c < n; c++) {
            if ((mask & Components.bit(c)) == 0) continue;
            int len = this.capacity * reg.arity(c);
            switch (reg.kind(c)) {
                case INT    -> intCols[c]    = new int[len];
                case LONG   -> longCols[c]   = new long[len];
                case DOUBLE -> doubleCols[c] = new double[len];
            }
        }
        rowToEntity = new int[this.capacity];
    }

    public int size() { return size; }
    public int capacity() { return capacity; }
    public boolean has(int comp) { return (mask & Components.bit(comp)) != 0; }

    public int[]    intCol(int comp)    { return intCols[comp]; }
    public long[]   longCol(int comp)   { return longCols[comp]; }
    public double[] doubleCol(int comp) { return doubleCols[comp]; }

    // Пакетные: View берёт таблицы колонок ОДИН раз на bind и дальше индексирует их напрямую,
    // не заходя в store на каждом обращении. Массивы отдаются как есть (не копия) — это горячий
    // путь; законно, потому что на время стадии строки заморожены и колонки не переаллоцируются.
    int[][]    intCols()    { return intCols; }
    long[][]   longCols()   { return longCols; }
    double[][] doubleCols() { return doubleCols; }

    /** localRow → стабильный entityId. Обратная сторона карты World.entityRow. */
    public int entityAt(int row) {
        if (row < 0 || row >= size) throw new IndexOutOfBoundsException("row=" + row + " size=" + size);
        return rowToEntity[row];
    }

    /** Добавляет строку под entityId, возвращает localRow. Значения компонентов — нули. */
    public int addRow(int entityId) {
        if (size == capacity) grow(capacity * 2);
        rowToEntity[size] = entityId;
        return size++;
    }

    /**
     * Удаляет строку, затыкая дыру ПОСЛЕДНЕЙ строкой (swap-remove, O(1)).
     * Возвращает entityId переехавшей строки (её localRow теперь = row), либо -1 если удалили
     * последнюю. Вызывающий ОБЯЗАН обновить карту для переехавшего — иначе она разъедется молча.
     * Именно эта перестановка и требует layout-independent checksum.
     */
    public int swapRemove(int row) {
        if (row < 0 || row >= size) throw new IndexOutOfBoundsException("row=" + row + " size=" + size);
        int last = size - 1;
        int moved = -1;
        if (row != last) {
            copyRow(last, row);
            moved = rowToEntity[last];
            rowToEntity[row] = moved;
        }
        size--;
        return moved;
    }

    /** Копирует все компоненты строки src в dst внутри этого архетипа. */
    private void copyRow(int src, int dst) {
        for (int c = 0; c < reg.count(); c++) {
            if ((mask & Components.bit(c)) == 0) continue;
            int a = reg.arity(c);
            switch (reg.kind(c)) {
                case INT    -> System.arraycopy(intCols[c],    src * a, intCols[c],    dst * a, a);
                case LONG   -> System.arraycopy(longCols[c],   src * a, longCols[c],   dst * a, a);
                case DOUBLE -> System.arraycopy(doubleCols[c], src * a, doubleCols[c], dst * a, a);
            }
        }
    }

    /**
     * Переносит значения ОБЩИХ компонентов строки src этого архетипа в строку dst другого.
     * Компоненты, которых нет в целевой маске, отбрасываются; новых — остаются нулями.
     * Это и есть смена архетипа на уровне данных.
     */
    public void copyRowTo(int src, ArchetypeStore dst, int dstRow) {
        long common = mask & dst.mask;
        for (int c = 0; c < reg.count(); c++) {
            if ((common & Components.bit(c)) == 0) continue;
            int a = reg.arity(c);
            switch (reg.kind(c)) {
                case INT    -> System.arraycopy(intCols[c],    src * a, dst.intCols[c],    dstRow * a, a);
                case LONG   -> System.arraycopy(longCols[c],   src * a, dst.longCols[c],   dstRow * a, a);
                case DOUBLE -> System.arraycopy(doubleCols[c], src * a, dst.doubleCols[c], dstRow * a, a);
            }
        }
    }

    /** Глубокая копия: reference-прогон против parallel идут по независимым данным. */
    public ArchetypeStore copy() {
        ArchetypeStore c = new ArchetypeStore(reg, mask, capacity);
        for (int comp = 0; comp < reg.count(); comp++) {
            if ((mask & Components.bit(comp)) == 0) continue;
            switch (reg.kind(comp)) {
                case INT    -> System.arraycopy(intCols[comp],    0, c.intCols[comp],    0, intCols[comp].length);
                case LONG   -> System.arraycopy(longCols[comp],   0, c.longCols[comp],   0, longCols[comp].length);
                case DOUBLE -> System.arraycopy(doubleCols[comp], 0, c.doubleCols[comp], 0, doubleCols[comp].length);
            }
        }
        System.arraycopy(rowToEntity, 0, c.rowToEntity, 0, size);
        c.size = size;
        return c;
    }

    private void grow(int newCap) {
        for (int c = 0; c < reg.count(); c++) {
            if ((mask & Components.bit(c)) == 0) continue;
            int len = newCap * reg.arity(c);
            switch (reg.kind(c)) {
                case INT    -> intCols[c]    = Arrays.copyOf(intCols[c], len);
                case LONG   -> longCols[c]   = Arrays.copyOf(longCols[c], len);
                case DOUBLE -> doubleCols[c] = Arrays.copyOf(doubleCols[c], len);
            }
        }
        rowToEntity = Arrays.copyOf(rowToEntity, newCap);
        capacity = newCap;
    }
}
