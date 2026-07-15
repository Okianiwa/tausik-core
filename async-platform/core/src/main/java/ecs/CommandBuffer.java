package ecs;

import java.util.Arrays;

/**
 * Thread-local буфер отложенных эффектов одной задачи (система × чанк энтити).
 * Эффекты на ЧУЖОЙ инвентарь (хоппер толкает предмет в соседа) нельзя писать на месте
 * в параллельной фазе — они уходят сюда и применяются в упорядоченной apply-фазе.
 * Тег (systemOrder, chunkStart): буфер уже отсортирован внутри (энтити по возрастанию),
 * глобальный детерминизм — сортировкой БУФЕРОВ (их немного), без per-command сортировки.
 */
public final class CommandBuffer {
    public static final int OP_ADD = 0;  // коммутативная (перекладка предметов)
    public static final int OP_SET = 1;  // порядко-зависимая (last-writer по расписанию)

    public final int systemOrder;
    public final int chunkStart;

    int[] op     = new int[8];
    int[] entity = new int[8];
    int[] slot   = new int[8];
    long[] value = new long[8];
    int n = 0;

    public CommandBuffer(int systemOrder, int chunkStart) {
        this.systemOrder = systemOrder;
        this.chunkStart = chunkStart;
    }

    public void addInv(int entity, int slot, long amount) { push(OP_ADD, entity, slot, amount); }
    public void setInv(int entity, int slot, long v)      { push(OP_SET, entity, slot, v); }

    private void push(int o, int e, int s, long v) {
        if (n == op.length) {
            int c = n * 2;
            op = Arrays.copyOf(op, c);
            entity = Arrays.copyOf(entity, c);
            slot = Arrays.copyOf(slot, c);
            value = Arrays.copyOf(value, c);
        }
        op[n] = o; entity[n] = e; slot[n] = s; value[n] = v; n++;
    }
}
