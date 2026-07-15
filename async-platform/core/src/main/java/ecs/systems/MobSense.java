package ecs.systems;

import ecs.Archetype;
import ecs.CommandBuffer;
import ecs.Components;
import ecs.GameSystem;
import ecs.View;

/**
 * Разделение (separation): моб читает позиции K соседей и рулит скоростью ПРОЧЬ от них.
 * ГРЯЗНАЯ подсистема: читает POSITION чужих строк в СВОЁМ архетипе — это то, что чистые
 * блок-энтити не делали. Пишет только VELOCITY (своя строка), поэтому safe при параллели,
 * пока никто не пишет POSITION в этой стадии (writer POSITION уходит в следующую стадию).
 */
public final class MobSense implements GameSystem {
    private static final int K = 8;
    private static final double SPEED = 1.0;

    public String name() { return "MobSense"; }
    public int archetype() { return Archetype.MOB; }
    public long reads()  { return Components.mask(Components.POSITION); }
    public long writes() { return Components.mask(Components.VELOCITY); }

    public void run(View v, int e, CommandBuffer cb) {
        int n = v.size();
        double x = v.posX(e), y = v.posY(e), sx = 0, sy = 0;
        for (int k = 1; k <= K; k++) {
            int j = (e + k) % n;
            double dx = x - v.posX(j), dy = y - v.posY(j);
            double d2 = dx * dx + dy * dy + 1e-6;
            sx += dx / d2; sy += dy / d2; // inverse-distance отталкивание
        }
        double len = Math.sqrt(sx * sx + sy * sy) + 1e-9;
        v.setVelX(e, sx / len * SPEED);
        v.setVelY(e, sy / len * SPEED);
    }
}
