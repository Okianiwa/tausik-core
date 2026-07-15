package ecs;

/**
 * Доступ системы к миру, проверяющий контракт read/write НА КАЖДОМ обращении.
 * Именно эта валидация позволяет планировщику доверять объявлениям и параллелить безопасно.
 * Проверка = один тест битовой маски + ветка → O(1), фактически бесплатна (kill-критерий #3).
 */
public final class View {
    private final World w;
    private long reads, writes;

    public View(World w) { this.w = w; }

    /** Привязать к объявленным множествам текущей системы. */
    public View bind(long reads, long writes) { this.reads = reads; this.writes = writes; return this; }

    private void checkRead(int comp) {
        if (((reads | writes) & World.bit(comp)) == 0)
            throw new ContractViolation("read необъявленного компонента " + comp);
    }
    private void checkWrite(int comp) {
        if ((writes & World.bit(comp)) == 0)
            throw new ContractViolation("write необъявленного компонента " + comp);
    }

    public int posX(int e) { checkRead(World.POS); return w.posX[e]; }
    public int posY(int e) { checkRead(World.POS); return w.posY[e]; }
    public int posZ(int e) { checkRead(World.POS); return w.posZ[e]; }

    public long energy(int e)        { checkRead(World.ENERGY);  return w.energy[e]; }
    public void setEnergy(int e, long v) { checkWrite(World.ENERGY); w.energy[e] = v; }

    public double heat(int e)        { checkRead(World.HEAT);     return w.heat[e]; }
    public void setHeat(int e, double v) { checkWrite(World.HEAT); w.heat[e] = v; }

    public int progress(int e)       { checkRead(World.PROGRESS); return w.progress[e]; }
    public void setProgress(int e, int v){ checkWrite(World.PROGRESS); w.progress[e] = v; }

    public long inv(int e)           { checkRead(World.INV);      return w.inv[e]; }
    public void setInv(int e, long v){ checkWrite(World.INV);     w.inv[e] = v; }
}
