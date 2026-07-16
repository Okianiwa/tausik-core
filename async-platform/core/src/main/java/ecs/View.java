package ecs;

/**
 * Доступ системы к СВОЕМУ архетипу с проверкой контракта read/write на КАЖДОМ обращении.
 * Проверка = тест битовой маски + ветка (O(1), kill-критерий #3 Phase 0 — фактически бесплатна).
 * Именно она позволяет планировщику доверять объявлениям и параллелить безопасно.
 *
 * Аксессоры GENERIC (getInt/getLong/getDouble + сеттеры), а не именованные по компоненту:
 * иначе каждый новый компонент правил бы этот класс и AC #2 не выполнялся бы. Цена — одна
 * лишняя индирекция (s.intCol(c) вместо w.posX): comp в месте вызова константа, JIT её хойстит.
 *
 * Индекс — localRow ВНУТРИ архетипа, не глобальная строка мира. Соседи (MobSense, RedstonePropagate)
 * читаются по такому же localRow: доступ всегда внутри своего архетипа, cross-archetype — только
 * через CommandBuffer.
 */
public final class View {
    private final int[] arity;
    private ArchetypeStore s;
    private long reads, writes;

    public View(ComponentRegistry reg) {
        arity = new int[reg.count()];
        for (int c = 0; c < reg.count(); c++) arity[c] = reg.arity(c);
    }

    public View bind(ArchetypeStore store, long reads, long writes) {
        this.s = store;
        this.reads = reads;
        this.writes = writes;
        return this;
    }

    /** Размер СВОЕГО архетипа (для обёртки индексов соседей). */
    public int size() { return s.size(); }

    /** localRow → стабильный entityId. Нужен для адресации целей CommandBuffer. */
    public int entityAt(int row) { return s.entityAt(row); }

    private void checkRead(int comp) {
        if (((reads | writes) & Components.bit(comp)) == 0)
            throw new ContractViolation("read необъявленного компонента " + comp);
    }

    private void checkWrite(int comp) {
        if ((writes & Components.bit(comp)) == 0)
            throw new ContractViolation("write необъявленного компонента " + comp);
    }

    public int getInt(int comp, int row) { checkRead(comp); return s.intCol(comp)[row * arity[comp]]; }
    public int getInt(int comp, int row, int lane) { checkRead(comp); return s.intCol(comp)[row * arity[comp] + lane]; }
    public void setInt(int comp, int row, int v) { checkWrite(comp); s.intCol(comp)[row * arity[comp]] = v; }
    public void setInt(int comp, int row, int lane, int v) { checkWrite(comp); s.intCol(comp)[row * arity[comp] + lane] = v; }

    public long getLong(int comp, int row) { checkRead(comp); return s.longCol(comp)[row * arity[comp]]; }
    public void setLong(int comp, int row, long v) { checkWrite(comp); s.longCol(comp)[row * arity[comp]] = v; }

    public double getDouble(int comp, int row) { checkRead(comp); return s.doubleCol(comp)[row * arity[comp]]; }
    public double getDouble(int comp, int row, int lane) { checkRead(comp); return s.doubleCol(comp)[row * arity[comp] + lane]; }
    public void setDouble(int comp, int row, double v) { checkWrite(comp); s.doubleCol(comp)[row * arity[comp]] = v; }
    public void setDouble(int comp, int row, int lane, double v) { checkWrite(comp); s.doubleCol(comp)[row * arity[comp] + lane] = v; }

    /**
     * Диагностическая busy-work в scratch-компонент BUSY (вне контракта, вне checksum) —
     * регулятор веса per-entity работы из среза 3. Контракт намеренно НЕ проверяется: BUSY не
     * объявляется системами. Отсутствие колонки — ошибка сборки сцены, поэтому падаем громко.
     */
    public void busy(int row) {
        if (Work.WEIGHT <= 0) return;
        long[] col = s.longCol(Components.BUSY);
        if (col == null)
            throw new IllegalStateException("Архетип " + Long.toBinaryString(s.mask)
                    + " без компонента BUSY, но система вызывает busy() при Work.WEIGHT=" + Work.WEIGHT);
        col[row] = Work.spin(row * 2654435761L + col[row]);
    }
}
