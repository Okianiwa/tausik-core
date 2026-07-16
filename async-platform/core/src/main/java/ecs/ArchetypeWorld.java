package ecs;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * Контейнер архетипов + карта стабильный entityId ↔ (архетип, localRow).
 * Заменяет фикс-колонки старого World: знание о конкретных компонентах живёт в ComponentRegistry,
 * данные — в ArchetypeStore по маске. Новая подсистема больше не правит этот класс.
 *
 * ВРЕМЕННОЕ ИМЯ: станет World на шаге 9, когда фикс-колоночный World будет удалён. Пока оба живут
 * параллельно — иначе рефакторинг такого размера не имел бы ни одного зелёного промежуточного шага.
 */
public final class ArchetypeWorld {
    public final ComponentRegistry reg;
    private final List<ArchetypeStore> stores = new ArrayList<>();

    /**
     * entityId → локация, УПАКОВАННАЯ в один long: (индекс в stores << 32) | localRow.
     * Одна карта, а не две (entityStore[] + entityRow[]) — потому что применение команд на барьере
     * резолвит цель случайным доступом (хоппер шлёт ~50k/тик), и две карты по 600КБ стоили ДВУХ
     * зависимых промахов кэша на команду вместо одного. Это серийная фаза, Амдаль наказывает её
     * вдвойне (замер сессии #4). Индирекция как таковая неустранима: стабильный id в archetype-модели
     * перестал быть номером строки — но платить за неё дважды не обязательно.
     */
    private long[] entityLoc;
    private int entityCount;

    private static final long UNPLACED = -1L;

    private static long pack(int storeIdx, int row) { return ((long) storeIdx << 32) | (row & 0xFFFFFFFFL); }
    static int storeIdxOf(long loc) { return (int) (loc >>> 32); }
    static int rowOfLoc(long loc)   { return (int) loc; }

    public ArchetypeWorld(ComponentRegistry reg, int expectedEntities) {
        this.reg = reg;
        int cap = Math.max(1, expectedEntities);
        entityLoc = new long[cap];
        Arrays.fill(entityLoc, UNPLACED);
    }

    public int entityCount() { return entityCount; }
    public List<ArchetypeStore> stores() { return stores; }
    public int storeCount() { return stores.size(); }

    /** Индекс архетипа по маске; создаёт при первом обращении. Архетипов единицы — линейный поиск дешевле хеша. */
    public int storeIndex(long mask) {
        for (int i = 0; i < stores.size(); i++) if (stores.get(i).mask == mask) return i;
        stores.add(new ArchetypeStore(reg, mask, 16));
        return stores.size() - 1;
    }

    public ArchetypeStore store(long mask) { return stores.get(storeIndex(mask)); }
    public ArchetypeStore storeAt(int index) { return stores.get(index); }

    public ArchetypeStore storeOf(int entityId) { return stores.get(storeIdxOf(entityLoc[checkAlive(entityId)])); }
    public int storeIndexOf(int entityId) { return storeIdxOf(entityLoc[checkAlive(entityId)]); }
    public int rowOf(int entityId) { return rowOfLoc(entityLoc[checkAlive(entityId)]); }

    // Быстрый путь applyOrdered — единственный горячий серийный резолв в системе. Публичные
    // storeOf/rowOf на команду дают двойной checkAlive + ArrayList.get с checkcast; здесь
    // вызывающий берёт карту один раз и платит один индекс на команду.
    // Пакетный, не API: снаружи резолв обязан идти через проверяющие storeOf/rowOf.
    long[] entityLocMap() { return entityLoc; }

    /**
     * Создаёт энтити в архетипе mask, возвращает СТАБИЛЬНЫЙ entityId.
     * Это построение сцены, а не deferred-структурное изменение из параллельной фазы
     * (те — вне объёма задачи, см. scope_exclude).
     */
    public int createEntity(long mask) {
        int id = entityCount++;
        ensureCapacity(id + 1);
        int si = storeIndex(mask);
        entityLoc[id] = pack(si, stores.get(si).addRow(id));
        return id;
    }

    /**
     * ИНВАРИАНТ C: пока стадия исполняется, карта архетип→строки ЗАМОРОЖЕНА.
     * На нём стоит корректность планировщика: он пустил системы параллельно, доказав, что их
     * архетипы не пересекаются, и системы читают соседей по localRow (MobSense — 16 чтений на моба).
     * Переставь строку под ними — и оба факта разом станут ложью, ТИХО. Поэтому строки двигаются
     * только на барьере, а нарушение обязано падать громко, а не портить данные.
     *
     * volatile: флаг ставит главный поток, читают рабочие. Формально happens-before уже даёт сама
     * отправка задач в пул, но флаг — часть контракта, а не оптимизация; цена нулевая (раз на стадию).
     */
    private volatile boolean rowsFrozen;

    void freezeRows() { rowsFrozen = true; }
    void thawRows()   { rowsFrozen = false; }
    public boolean rowsFrozen() { return rowsFrozen; }

    /**
     * Смена архетипа: swap-remove из старого + append в новый, общие компоненты переносятся.
     * ПЕРЕСТАВЛЯЕТ СТРОКИ — переехавшему соседу чинится карта. Именно поэтому checksum обязан
     * ключеваться на entityId, а не на индексе строки (иначе перестановка = ложное расхождение).
     * Разрешена ТОЛЬКО на барьере — см. инвариант C выше.
     */
    public void migrate(int entityId, long newMask) {
        if (rowsFrozen)
            throw new ContractViolation("migrate(" + entityId + ") внутри исполняющейся стадии: "
                    + "строки заморожены до барьера (инвариант C). Структурные изменения — только в apply.");
        int id = checkAlive(entityId);
        long loc = entityLoc[id];
        int oldIdx = storeIdxOf(loc);
        ArchetypeStore old = stores.get(oldIdx);
        if (old.mask == newMask) return;

        int newIdx = storeIndex(newMask);
        ArchetypeStore dst = stores.get(newIdx);
        int oldRow = rowOfLoc(loc);

        int dstRow = dst.addRow(id);
        old.copyRowTo(oldRow, dst, dstRow);

        int moved = old.swapRemove(oldRow);
        if (moved >= 0) entityLoc[moved] = pack(oldIdx, oldRow); // переехавший занял освободившуюся строку

        entityLoc[id] = pack(newIdx, dstRow);
    }

    /**
     * Детерминированный чек-сумм. Ключ — СТАБИЛЬНЫЙ entityId, не индекс строки: физическая
     * раскладка (swap-remove, порядок создания архетипов) на него не влияет. Старый World хешировал
     * в row-order — при подвижных строках это дало бы расхождение ref vs parallel при ИДЕНТИЧНОМ
     * состоянии, то есть детерминизм-тест врал бы. Scratch-компоненты исключает реестр (inChecksum).
     */
    public long checksum() {
        long h = 1125899906842597L;
        for (int id = 0; id < entityCount; id++) {
            long loc = entityLoc[id];
            int si = storeIdxOf(loc);
            if (si < 0) continue;
            ArchetypeStore s = stores.get(si);
            int row = rowOfLoc(loc);
            h = 31 * h + id;
            h = 31 * h + s.mask; // архетип — часть состояния: смена набора компонентов видна в хеше
            for (int c = 0; c < reg.count(); c++) {
                if (!s.has(c) || !reg.inChecksum(c)) continue;
                int a = reg.arity(c);
                for (int lane = 0; lane < a; lane++) {
                    int i = row * a + lane;
                    switch (reg.kind(c)) {
                        case INT    -> h = 31 * h + s.intCol(c)[i];
                        case LONG   -> h = 31 * h + s.longCol(c)[i];
                        case DOUBLE -> h = 31 * h + Double.doubleToLongBits(s.doubleCol(c)[i]);
                    }
                }
            }
        }
        return h;
    }

    /** Глубокая копия мира — reference-прогон против parallel идут по независимым данным. */
    public ArchetypeWorld copy() {
        ArchetypeWorld w = new ArchetypeWorld(reg, Math.max(1, entityCount));
        for (ArchetypeStore s : stores) w.stores.add(s.copy());
        w.ensureCapacity(Math.max(1, entityCount));
        System.arraycopy(entityLoc, 0, w.entityLoc, 0, entityCount);
        w.entityCount = entityCount;
        return w;
    }

    private int checkAlive(int entityId) {
        if (entityId < 0 || entityId >= entityCount)
            throw new IndexOutOfBoundsException("entityId=" + entityId + " count=" + entityCount);
        if (storeIdxOf(entityLoc[entityId]) < 0)
            throw new IllegalStateException("entityId=" + entityId + " не размещён в архетипе");
        return entityId;
    }

    private void ensureCapacity(int need) {
        if (need <= entityLoc.length) return;
        int cap = Math.max(need, entityLoc.length * 2);
        int from = entityLoc.length;
        entityLoc = Arrays.copyOf(entityLoc, cap);
        Arrays.fill(entityLoc, from, cap, UNPLACED);
    }
}
