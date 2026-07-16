package ecs.systems;

import ecs.CommandBuffer;
import ecs.Components;
import ecs.GameSystem;
import ecs.View;

/**
 * Старение дропа: HEALTH здесь — остаток жизни в тиках. Дошёл до нуля — предмет исчезает.
 * Вторая половина структурной текучки MC: дропы не копятся вечно, они деспавнятся.
 *
 * HEALTH пишется НАПРЯМУЮ (своя строка, своего архетипа — это разрешено контрактом), а destroy
 * уходит в буфер: удаление переставляет строки swap-remove'ом, а внутри стадии они заморожены
 * (инвариант C). Поэтому оно и обязано ждать барьера.
 */
public final class DropAge implements GameSystem {

    public String name() { return "DropAge"; }
    public long reads()  { return 0; }
    public long writes() { return Components.mask(Components.HEALTH); }

    public void run(View v, int row, CommandBuffer cb) {
        int left = v.getInt(Components.HEALTH, row) - 1;
        v.setInt(Components.HEALTH, row, left);
        if (left <= 0) cb.destroy(v.entityAt(row));
    }
}
