package ecs.systems;

import ecs.CommandBuffer;
import ecs.Components;
import ecs.GameSystem;
import ecs.View;

/**
 * Тик машины: тратит ENERGY, двигает прогресс, по завершении перекладывает
 * input→output в своём инвентаре. По ТИПУ {PROGRESS, INVENTORY} пересекается с печью, но
 * архетипы разные (у машины ENERGY, у печи RECIPE/HEAT/FUEL) → конфликта нет, одна стадия.
 * Гейт «не машина» по energy<=0 убран: архетип матчится по компонентам.
 */
public final class MachineProcess implements GameSystem {
    private static final long COST = 10;
    private static final int MACHINE_TICKS = 100;

    public String name() { return "MachineProcess"; }
    public long reads()  { return 0; }
    public long writes() { return Components.mask(Components.PROGRESS, Components.INVENTORY, Components.ENERGY); }

    public void run(View v, int e, CommandBuffer cb) {
        v.busy(e); // диагностический вес (Work.WEIGHT), 0 по умолчанию

        long energy = v.getLong(Components.ENERGY, e);
        if (energy >= COST && v.getInt(Components.INVENTORY, e, Components.SLOT_INPUT) > 0) {
            v.setLong(Components.ENERGY, e, energy - COST);
            int p = v.getInt(Components.PROGRESS, e) + 1;
            if (p >= MACHINE_TICKS) {
                v.setInt(Components.INVENTORY, e, Components.SLOT_INPUT,
                        v.getInt(Components.INVENTORY, e, Components.SLOT_INPUT) - 1);
                v.setInt(Components.INVENTORY, e, Components.SLOT_OUTPUT,
                        v.getInt(Components.INVENTORY, e, Components.SLOT_OUTPUT) + 1);
                v.setInt(Components.PROGRESS, e, 0);
            } else {
                v.setInt(Components.PROGRESS, e, p);
            }
        }
    }
}
