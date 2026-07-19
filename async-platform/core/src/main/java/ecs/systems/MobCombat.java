package ecs.systems;

import ecs.CommandBuffer;
import ecs.Components;
import ecs.GameSystem;
import ecs.View;

/**
 * Урон от давки: моб читает позиции K соседей, при скученности теряет здоровье.
 * Читает POSITION (соседи), пишет HEALTH (своя строка). С MobSense НЕ конфликтует
 * (оба читают POSITION — read∩read, пишут разное) → пакуются в ОДНУ стадию. Это и есть
 * нетривиальная раскладка грязной подсистемы: частичная упаковка, а не полная сериализация.
 */
public final class MobCombat implements GameSystem {
    private static final int K = 8;
    private static final double R2 = 25.0;   // радиус давки²
    private static final int CROWD = 3;

    public String name() { return "MobCombat"; }
    public long reads()  { return Components.mask(Components.POSITION); }
    public long writes() { return Components.mask(Components.HEALTH); }

    public void run(View v, int e, CommandBuffer cb) {
        int n = v.size();
        double x = v.getDouble(Components.POSITION, e, Components.LANE_X);
        double y = v.getDouble(Components.POSITION, e, Components.LANE_Y);
        int crowd = 0;
        for (int k = 1; k <= K; k++) {
            int j = (e + k) % n;
            double dx = x - v.getDouble(Components.POSITION, j, Components.LANE_X);
            double dy = y - v.getDouble(Components.POSITION, j, Components.LANE_Y);
            if (dx * dx + dy * dy < R2) crowd++;
        }
        int hp = v.getInt(Components.HEALTH, e);
        if (crowd >= CROWD && hp > 0) v.setInt(Components.HEALTH, e, hp - 1);
    }
}
