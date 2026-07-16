package ecs.systems;

import ecs.CommandBuffer;
import ecs.Components;
import ecs.GameSystem;
import ecs.View;

/**
 * Интеграция позиции по скорости, тор-обёртка в поле [0,FIELD). Читает VELOCITY (её пишет
 * MobSense) и POSITION (свою строку), пишет POSITION. Конфликтует и с MobSense (VELOCITY r∩w,
 * POSITION w∩r), поэтому уходит в отдельную стадию — writer POSITION нельзя рядом с читателями.
 */
public final class MobMove implements GameSystem {
    private static final double FIELD = 1000.0;

    public String name() { return "MobMove"; }
    public long reads()  { return Components.mask(Components.VELOCITY); }
    public long writes() { return Components.mask(Components.POSITION); }

    public void run(View v, int e, CommandBuffer cb) {
        double x = wrap(v.getDouble(Components.POSITION, e, Components.LANE_X)
                + v.getDouble(Components.VELOCITY, e, Components.LANE_X));
        double y = wrap(v.getDouble(Components.POSITION, e, Components.LANE_Y)
                + v.getDouble(Components.VELOCITY, e, Components.LANE_Y));
        v.setDouble(Components.POSITION, e, Components.LANE_X, x);
        v.setDouble(Components.POSITION, e, Components.LANE_Y, y);
    }

    private static double wrap(double x) { return x - FIELD * Math.floor(x / FIELD); }
}
