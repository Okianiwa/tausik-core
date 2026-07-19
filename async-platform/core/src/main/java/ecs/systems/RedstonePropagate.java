package ecs.systems;

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

    /** Ширина grid — топология сцены, не компонент. Задаётся сценой при сборке. */
    private final int gridWidth;

    public RedstonePropagate(int gridWidth) { this.gridWidth = gridWidth; }

    public String name() { return "RedstonePropagate"; }
    public long reads()  { return Components.mask(Components.POWER, Components.SOURCE); }
    public long writes() { return Components.mask(Components.POWER_NEXT); }

    public void run(View v, int e, CommandBuffer cb) {
        if (v.getInt(Components.SOURCE, e) == 1) { v.setInt(Components.POWER_NEXT, e, MAX); return; }
        int w = gridWidth;
        int x = e % w;
        int n = v.size();
        int best = 0;
        if (x > 0)      best = Math.max(best, v.getInt(Components.POWER, e - 1)); // left
        if (x < w - 1)  best = Math.max(best, v.getInt(Components.POWER, e + 1)); // right
        if (e - w >= 0) best = Math.max(best, v.getInt(Components.POWER, e - w)); // up
        if (e + w < n)  best = Math.max(best, v.getInt(Components.POWER, e + w)); // down
        v.setInt(Components.POWER_NEXT, e, Math.max(0, best - 1));
    }
}
