package ecs.systems;

import ecs.CommandBuffer;
import ecs.Components;
import ecs.GameSystem;
import ecs.View;

/**
 * Плавка печи: жжёт топливо (burnTime), двигает прогресс при достаточном нагреве,
 * по завершении рецепта перекладывает input→output в СВОЁМ инвентаре.
 * Пишет свои колонки напрямую (disjoint строки в стадии) — эффектов на чужих нет.
 */
public final class FurnaceSmelt implements GameSystem {
    private static final int BURN_REFILL = 200;
    private static final double MIN_HEAT = 400.0;

    public String name() { return "FurnaceSmelt"; }
    public int archetype() { return ecs.Archetype.FURNACE; }
    public long reads()  { return Components.mask(Components.RECIPE, Components.HEAT); }
    public long writes() { return Components.mask(Components.PROGRESS, Components.INVENTORY, Components.FUEL); }

    public void run(View v, int e, CommandBuffer cb) {
        if (v.recipeTicks(e) <= 0) return; // не печь (архетип гейтится данными)

        int burn = v.burnTime(e);
        if (burn <= 0) {
            if (v.inv(e, Components.SLOT_FUEL) > 0) {
                v.setInv(e, Components.SLOT_FUEL, v.inv(e, Components.SLOT_FUEL) - 1);
                v.setBurnTime(e, BURN_REFILL);
            } else {
                if (v.progress(e) > 0) v.setProgress(e, 0);
                return;
            }
        } else {
            v.setBurnTime(e, burn - 1);
        }

        boolean canSmelt = v.inv(e, Components.SLOT_INPUT) > 0 && v.heat(e) >= MIN_HEAT;
        if (!canSmelt) {
            if (v.progress(e) > 0) v.setProgress(e, 0);
            return;
        }
        int p = v.progress(e) + 1;
        if (p >= v.recipeTicks(e)) {
            v.setInv(e, Components.SLOT_INPUT,  v.inv(e, Components.SLOT_INPUT)  - 1);
            v.setInv(e, Components.SLOT_OUTPUT, v.inv(e, Components.SLOT_OUTPUT) + 1);
            v.setProgress(e, 0);
        } else {
            v.setProgress(e, p);
        }
    }
}
