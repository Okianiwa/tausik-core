package ecs.systems;

import ecs.Archetype;
import ecs.CommandBuffer;
import ecs.Components;
import ecs.GameSystem;
import ecs.View;

/**
 * Один проход распространения редстоуна: клетка = source?15 : max(соседи)-1 (decay).
 * ORDERED-каскад: результат зависит от порядка → нельзя писать POWER на месте в параллели.
 * Решение — DOUBLE-BUFFER: читаем стабильный POWER (frozen), пишем POWER_NEXT (своя клетка).
 * Reads/writes разных компонентов → нет гонки; один проход двигает сигнал на 1 клетку.
 * Полное распространение = fixpoint из K проходов (K = дистанция сигнала) со swap между ними.
 */
public final class RedstonePropagate implements GameSystem {
    public static final int MAX = 15;

    public String name() { return "RedstonePropagate"; }
    public int archetype() { return Archetype.REDSTONE; }
    public long reads()  { return Components.mask(Components.POWER, Components.SOURCE); }
    public long writes() { return Components.mask(Components.POWER_NEXT); }

    public void run(View v, int e, CommandBuffer cb) {
        if (v.source(e) == 1) { v.setPowerNext(e, MAX); return; }
        int w = v.gridWidth();
        int x = e % w;
        int n = v.size();
        int best = 0;
        if (x > 0)         best = Math.max(best, v.power(e - 1)); // left
        if (x < w - 1)     best = Math.max(best, v.power(e + 1)); // right
        if (e - w >= 0)    best = Math.max(best, v.power(e - w)); // up
        if (e + w < n)     best = Math.max(best, v.power(e + w)); // down
        v.setPowerNext(e, Math.max(0, best - 1));
    }
}
