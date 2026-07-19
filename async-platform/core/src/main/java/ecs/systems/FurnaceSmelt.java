package ecs.systems;

import ecs.CommandBuffer;
import ecs.Components;
import ecs.GameSystem;
import ecs.View;

/**
 * Плавка печи: жжёт топливо (FUEL), двигает прогресс при достаточном нагреве,
 * по завершении рецепта перекладывает input→output в СВОЁМ инвентаре.
 * Пишет свои колонки напрямую (строки своего архетипа) — эффектов на чужих нет.
 * Гейт «не печь» по recipeTicks<=0 убран: архетип матчится по компонентам, чужих строк система
 * физически не видит — гейтить данными больше нечего.
 */
public final class FurnaceSmelt implements GameSystem {
    private static final int BURN_REFILL = 200;
    private static final double MIN_HEAT = 400.0;

    public String name() { return "FurnaceSmelt"; }
    public long reads()  { return Components.mask(Components.RECIPE, Components.HEAT); }
    public long writes() { return Components.mask(Components.PROGRESS, Components.INVENTORY, Components.FUEL); }

    public void run(View v, int e, CommandBuffer cb) {
        v.busy(e); // диагностический вес (Work.WEIGHT), 0 по умолчанию

        int burn = v.getInt(Components.FUEL, e);
        if (burn <= 0) {
            if (v.getInt(Components.INVENTORY, e, Components.SLOT_FUEL) > 0) {
                v.setInt(Components.INVENTORY, e, Components.SLOT_FUEL,
                        v.getInt(Components.INVENTORY, e, Components.SLOT_FUEL) - 1);
                v.setInt(Components.FUEL, e, BURN_REFILL);
            } else {
                if (v.getInt(Components.PROGRESS, e) > 0) v.setInt(Components.PROGRESS, e, 0);
                return;
            }
        } else {
            v.setInt(Components.FUEL, e, burn - 1);
        }

        boolean canSmelt = v.getInt(Components.INVENTORY, e, Components.SLOT_INPUT) > 0
                && v.getDouble(Components.HEAT, e) >= MIN_HEAT;
        if (!canSmelt) {
            if (v.getInt(Components.PROGRESS, e) > 0) v.setInt(Components.PROGRESS, e, 0);
            return;
        }
        int p = v.getInt(Components.PROGRESS, e) + 1;
        if (p >= v.getInt(Components.RECIPE, e)) {
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
