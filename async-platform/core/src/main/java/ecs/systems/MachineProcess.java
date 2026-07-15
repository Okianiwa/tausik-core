package ecs.systems;

import ecs.CommandBuffer;
import ecs.Components;
import ecs.GameSystem;
import ecs.View;

/**
 * Электромашина: тратит энергию, двигает прогресс, по завершении перекладывает
 * input→output в своём инвентаре. Конфликтует с печью по ТИПУ {PROGRESS, INVENTORY},
 * хотя строки энтити не пересекаются — это и есть цена консервативной type-гранулярности.
 */
public final class MachineProcess implements GameSystem {
    private static final long COST = 10;
    private static final int MACHINE_TICKS = 100;

    public String name() { return "MachineProcess"; }
    public int archetype() { return ecs.Archetype.MACHINE; }
    public long reads()  { return 0; }
    public long writes() { return Components.mask(Components.PROGRESS, Components.INVENTORY, Components.ENERGY); }

    public void run(View v, int e, CommandBuffer cb) {
        if (v.energy(e) <= 0) return; // не машина (архетип гейтится данными)

        if (v.energy(e) >= COST && v.inv(e, Components.SLOT_INPUT) > 0) {
            v.setEnergy(e, v.energy(e) - COST);
            int p = v.progress(e) + 1;
            if (p >= MACHINE_TICKS) {
                v.setInv(e, Components.SLOT_INPUT,  v.inv(e, Components.SLOT_INPUT)  - 1);
                v.setInv(e, Components.SLOT_OUTPUT, v.inv(e, Components.SLOT_OUTPUT) + 1);
                v.setProgress(e, 0);
            } else {
                v.setProgress(e, p);
            }
        }
    }
}
