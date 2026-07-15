package ecs;

/**
 * Доступ системы к миру с проверкой контракта read/write на КАЖДОМ обращении.
 * Проверка = тест битовой маски + ветка (O(1), kill-критерий #3 Phase 0 — фактически бесплатна).
 * Именно она позволяет планировщику доверять объявлениям и параллелить безопасно.
 */
public final class View {
    private final World w;
    private long reads, writes;

    public View(World w) { this.w = w; }

    public View bind(long reads, long writes) { this.reads = reads; this.writes = writes; return this; }

    private void checkRead(int comp) {
        if (((reads | writes) & Components.bit(comp)) == 0)
            throw new ContractViolation("read необъявленного компонента " + comp);
    }
    private void checkWrite(int comp) {
        if ((writes & Components.bit(comp)) == 0)
            throw new ContractViolation("write необъявленного компонента " + comp);
    }

    // INVENTORY
    public int inv(int e, int slot) { checkRead(Components.INVENTORY); return w.inv[w.invIndex(e, slot)]; }
    public void setInv(int e, int slot, int v) { checkWrite(Components.INVENTORY); w.inv[w.invIndex(e, slot)] = v; }

    // ENERGY
    public long energy(int e) { checkRead(Components.ENERGY); return w.energy[e]; }
    public void setEnergy(int e, long v) { checkWrite(Components.ENERGY); w.energy[e] = v; }

    // PROGRESS
    public int progress(int e) { checkRead(Components.PROGRESS); return w.progress[e]; }
    public void setProgress(int e, int v) { checkWrite(Components.PROGRESS); w.progress[e] = v; }

    // HEAT
    public double heat(int e) { checkRead(Components.HEAT); return w.heat[e]; }
    public void setHeat(int e, double v) { checkWrite(Components.HEAT); w.heat[e] = v; }

    // FUEL (burnTime)
    public int burnTime(int e) { checkRead(Components.FUEL); return w.burnTime[e]; }
    public void setBurnTime(int e, int v) { checkWrite(Components.FUEL); w.burnTime[e] = v; }

    // RECIPE (read-only)
    public int recipeTicks(int e) { checkRead(Components.RECIPE); return w.recipeTicks[e]; }

    // LINK (read-only)
    public int link(int e) { checkRead(Components.LINK); return w.link[e]; }

    /** Диагностическая busy-work в scratch-приёмник (вне контракта, вне checksum). */
    public void busy(int e) {
        if (Work.WEIGHT > 0) w.busy[e] = Work.spin(e * 2654435761L + w.busy[e]);
    }
}
