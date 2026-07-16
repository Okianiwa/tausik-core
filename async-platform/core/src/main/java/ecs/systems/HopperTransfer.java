package ecs.systems;

import ecs.CommandBuffer;
import ecs.Components;
import ecs.GameSystem;
import ecs.View;

/**
 * Хоппер толкает 1 предмет из своего output в input соседа (LINK — стабильный entityId цели).
 * Эффект на ЧУЖОЙ инвентарь нельзя писать на месте в параллельной фазе → идёт в CommandBuffer
 * и применяется в упорядоченной apply-фазе. ЕДИНСТВЕННАЯ межархетипная ссылка в модели:
 * цель живёт в другом архетипе (печь), поэтому адресуется entityId и резолвится на барьере.
 */
public final class HopperTransfer implements GameSystem {
    public String name() { return "HopperTransfer"; }
    public long reads()  { return Components.mask(Components.INVENTORY, Components.LINK); }
    public long writes() { return 0; } // только отложенные эффекты через cmd

    public void run(View v, int e, CommandBuffer cb) {
        v.busy(e); // диагностический вес (Work.WEIGHT), 0 по умолчанию
        int target = v.getInt(Components.LINK, e);
        if (target < 0) return; // хоппер без цели — доменный случай, не гейт архетипа
        if (v.getInt(Components.INVENTORY, e, Components.SLOT_OUTPUT) > 0) {
            cb.addInv(v.entityAt(e), Components.SLOT_OUTPUT, -1);
            cb.addInv(target, Components.SLOT_INPUT, +1);
        }
    }
}
