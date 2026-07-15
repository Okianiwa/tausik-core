package ecs.systems;

import ecs.Archetype;
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
    public int archetype() { return Archetype.MOB; }
    public long reads()  { return Components.mask(Components.POSITION); }
    public long writes() { return Components.mask(Components.HEALTH); }

    public void run(View v, int e, CommandBuffer cb) {
        int n = v.size();
        double x = v.posX(e), y = v.posY(e);
        int crowd = 0;
        for (int k = 1; k <= K; k++) {
            int j = (e + k) % n;
            double dx = x - v.posX(j), dy = y - v.posY(j);
            if (dx * dx + dy * dy < R2) crowd++;
        }
        if (crowd >= CROWD && v.health(e) > 0) v.setHealth(e, v.health(e) - 1);
    }
}
