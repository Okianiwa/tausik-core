package ecs;

import java.util.Arrays;

/**
 * Thread-local буфер отложенных эффектов одной задачи (система × архетип × чанк строк).
 * Эффекты на ЧУЖОЙ инвентарь (хоппер толкает предмет в соседа) нельзя писать на месте
 * в параллельной фазе — они уходят сюда и применяются в упорядоченной apply-фазе.
 *
 * Тег (systemOrder, archMask, chunkIndex). Был (systemOrder, chunkStart), где chunkStart —
 * индекс строки: при подвижных строках он теряет смысл, а чанки теперь нумеруются внутри
 * архетипа, поэтому chunkStart перестал быть уникальным между архетипами.
 * Сортировка по МАСКЕ, а не по id создания архетипа: id зависит от истории создания store'ов,
 * маска — нет, поэтому тотальный порядок apply воспроизводим независимо от порядка сборки сцены.
 *
 * Буфер уже отсортирован внутри (строки по возрастанию), глобальный детерминизм — сортировкой
 * БУФЕРОВ (их немного), без per-command сортировки.
 *
 * Цель — СТАБИЛЬНЫЙ entityId, резолв происходит в applyOrdered. Резолвить при эмиссии (в
 * параллельной фазе, где строки заморожены) ПРОБОВАЛИ и ОТКАТИЛИ: замер сессии #4 показал ноль
 * выигрыша — цикл apply не имеет зависимостей между итерациями, поэтому внеочередное исполнение и
 * так перекрывает промахи кэша, они не латентность-bound. См. dead end.
 */
public final class CommandBuffer {
    public static final int OP_ADD = 0;  // коммутативная (перекладка предметов)
    public static final int OP_SET = 1;  // порядко-зависимая (last-writer по расписанию)

    public final int systemOrder;
    public final long archMask;
    public final int chunkIndex;

    int[] op     = new int[8];
    int[] entity = new int[8];  // СТАБИЛЬНЫЙ entityId: apply резолвит его в (архетип, строку)
    int[] slot   = new int[8];
    long[] value = new long[8];
    int n = 0;

    public CommandBuffer(int systemOrder, long archMask, int chunkIndex) {
        this.systemOrder = systemOrder;
        this.archMask = archMask;
        this.chunkIndex = chunkIndex;
    }

    public void addInv(int entityId, int slot, long amount) { push(OP_ADD, entityId, slot, amount); }
    public void setInv(int entityId, int slot, long v)      { push(OP_SET, entityId, slot, v); }

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
