package ecs;

import java.util.Arrays;

/**
 * Thread-local буфер отложенных команд одной задачи (система × чанк энтити).
 * Тегируется (systemOrder, chunkStart): буфер уже отсортирован внутри (энтити по возрастанию),
 * поэтому глобальный детерминированный порядок достигается сортировкой БУФЕРОВ (их немного),
 * а не отдельных команд — без per-command сортировки.
 */
public final class CommandBuffer {
    public static final int OP_ADD = 0;  // коммутативная (порядко-независимая)
    public static final int OP_SET = 1;  // порядко-ЗАВИСИМАЯ (проверяет детерминизм apply)

    public final int systemOrder;
    public final int chunkStart;

    int[] op     = new int[8];
    int[] target = new int[8];
    long[] value = new long[8];
    int n = 0;

    public CommandBuffer(int systemOrder, int chunkStart) {
        this.systemOrder = systemOrder;
        this.chunkStart = chunkStart;
    }

    public void add(int target, long amount) { push(OP_ADD, target, amount); }
    public void set(int target, long v)      { push(OP_SET, target, v); }

    private void push(int o, int t, long v) {
        if (n == op.length) {
            int c = n * 2;
            op = Arrays.copyOf(op, c);
            target = Arrays.copyOf(target, c);
            value = Arrays.copyOf(value, c);
        }
        op[n] = o; target[n] = t; value[n] = v; n++;
    }
}
