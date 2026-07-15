package ecs.systems;

import ecs.CommandBuffer;
import ecs.Components;
import ecs.GameSystem;
import ecs.View;

/**
 * Хоппер толкает 1 предмет из своего output в input соседа (link).
 * Эффект на ЧУЖОЙ инвентарь нельзя писать на месте в параллельной фазе → идёт в CommandBuffer
 * и применяется в упорядоченной apply-фазе. READ Inventory конфликтует с писателями (печь/машина).
 */
public final class HopperTransfer implements GameSystem {
    public String name() { return "HopperTransfer"; }
    public int archetype() { return ecs.Archetype.HOPPER; }
    public long reads()  { return Components.mask(Components.INVENTORY, Components.LINK); }
    public long writes() { return 0; } // только отложенные эффекты через cmd

    public void run(View v, int e, CommandBuffer cb) {
        v.busy(e); // диагностический вес (Work.WEIGHT), 0 по умолчанию
        int target = v.link(e);
        if (target < 0) return; // не хоппер
        if (v.inv(e, Components.SLOT_OUTPUT) > 0) {
            cb.addInv(e, Components.SLOT_OUTPUT, -1);
            cb.addInv(target, Components.SLOT_INPUT, +1);
        }
    }
}
