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

    public int size() { return w.size; } // для обёртки индексов соседей

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

    // POSITION (сущности; чтение соседа = posX(other))
    public double posX(int e) { checkRead(Components.POSITION); return w.posX[e]; }
    public double posY(int e) { checkRead(Components.POSITION); return w.posY[e]; }
    public void setPosX(int e, double v) { checkWrite(Components.POSITION); w.posX[e] = v; }
    public void setPosY(int e, double v) { checkWrite(Components.POSITION); w.posY[e] = v; }

    // VELOCITY
    public double velX(int e) { checkRead(Components.VELOCITY); return w.velX[e]; }
    public double velY(int e) { checkRead(Components.VELOCITY); return w.velY[e]; }
    public void setVelX(int e, double v) { checkWrite(Components.VELOCITY); w.velX[e] = v; }
    public void setVelY(int e, double v) { checkWrite(Components.VELOCITY); w.velY[e] = v; }

    // HEALTH
    public int health(int e) { checkRead(Components.HEALTH); return w.health[e]; }
    public void setHealth(int e, int v) { checkWrite(Components.HEALTH); w.health[e] = v; }

    // REDSTONE (double-buffer: читаем POWER, пишем POWER_NEXT)
    public int power(int e) { checkRead(Components.POWER); return w.power[e]; }
    public void setPowerNext(int e, int v) { checkWrite(Components.POWER_NEXT); w.powerNext[e] = v; }
    public int source(int e) { checkRead(Components.SOURCE); return w.source[e]; }
    public int gridWidth() { return w.gridWidth; }

    /** Диагностическая busy-work в scratch-приёмник (вне контракта, вне checksum). */
    public void busy(int e) {
        if (Work.WEIGHT > 0) w.busy[e] = Work.spin(e * 2654435761L + w.busy[e]);
    }
}
