package ecs;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Future;

/**
 * Из объявленных reads/writes строит стадии (жадная раскладка неконфликтующих систем)
 * и исполняет их: reference (однопоточно) и parallel (data-parallel по чанкам внутри стадии).
 * Детерминизм — фикс. порядок систем + упорядоченный apply отсортированных буферов.
 */
public final class Scheduler {
    public final List<GameSystem> systems;
    public final List<int[]> stages; // стадия = массив индексов систем (в стабильном порядке)

    public Scheduler(List<GameSystem> systems) {
        this.systems = systems;
        this.stages = plan(systems);
    }

    /** conflict(A,B) ⇔ (wA∩wB)∪(wA∩rB)∪(rA∩wB) ≠ ∅. read∩read — не конфликт. */
    public static List<int[]> plan(List<GameSystem> systems) {
        List<List<Integer>> stageSys = new ArrayList<>();
        List<long[]> stageMask = new ArrayList<>(); // [reads, writes]
        for (int i = 0; i < systems.size(); i++) {
            GameSystem s = systems.get(i);
            int placed = -1;
            for (int st = 0; st < stageSys.size(); st++) {
                long sr = stageMask.get(st)[0], sw = stageMask.get(st)[1];
                boolean conflict = (s.writes() & sr) != 0 || (s.writes() & sw) != 0 || (s.reads() & sw) != 0;
                if (!conflict) { placed = st; break; }
            }
            if (placed < 0) {
                stageSys.add(new ArrayList<>());
                stageMask.add(new long[]{0, 0});
                placed = stageSys.size() - 1;
            }
            stageSys.get(placed).add(i);
            stageMask.get(placed)[0] |= s.reads();
            stageMask.get(placed)[1] |= s.writes();
        }
        List<int[]> out = new ArrayList<>();
        for (List<Integer> l : stageSys) {
            int[] a = new int[l.size()];
            for (int k = 0; k < a.length; k++) a[k] = l.get(k);
            out.add(a);
        }
        return out;
    }

    /** Однопоточный эталон: энтити по возрастанию, системы в порядке стадий. */
    public void runReference(World w) {
        for (int[] stage : stages) {
            List<CommandBuffer> buffers = new ArrayList<>();
            for (int si : stage) {
                GameSystem s = systems.get(si);
                View v = new View(w).bind(s.reads(), s.writes());
                CommandBuffer cb = new CommandBuffer(si, 0);
                for (int e = 0; e < w.size; e++) s.run(v, e, cb);
                buffers.add(cb);
            }
            applyOrdered(w, buffers);
        }
    }

    /** Параллельно: внутри каждой стадии — независимые задачи (система × чанк энтити). */
    public void runParallel(World w, ExecutorService pool, int threads) throws Exception {
        int chunkCount = Math.max(1, threads);
        int[] bounds = chunkBounds(w.size, chunkCount);
        for (int[] stage : stages) {
            List<Callable<CommandBuffer>> tasks = new ArrayList<>();
            for (int si : stage) {
                final GameSystem s = systems.get(si);
                final int sysIdx = si;
                for (int c = 0; c < chunkCount; c++) {
                    final int lo = bounds[c], hi = bounds[c + 1];
                    tasks.add(() -> {
                        View v = new View(w).bind(s.reads(), s.writes());
                        CommandBuffer cb = new CommandBuffer(sysIdx, lo);
                        for (int e = lo; e < hi; e++) s.run(v, e, cb);
                        return cb;
                    });
                }
            }
            List<Future<CommandBuffer>> fs = pool.invokeAll(tasks);
            List<CommandBuffer> buffers = new ArrayList<>(fs.size());
            for (Future<CommandBuffer> f : fs) buffers.add(f.get());
            applyOrdered(w, buffers);
        }
    }

    private static int[] chunkBounds(int n, int chunks) {
        int[] b = new int[chunks + 1];
        int base = n / chunks, rem = n % chunks, idx = 0;
        for (int i = 0; i < chunks; i++) { b[i] = idx; idx += base + (i < rem ? 1 : 0); }
        b[chunks] = n;
        return b;
    }

    /**
     * Детерминированный упорядоченный apply: буферы сортируются по (systemOrder, chunkStart),
     * команды применяются в порядке эмиссии. Write-write на один таргет → last-writer по этому
     * тотальному порядку (для OP_SET). Порядок не зависит от того, какой поток когда закончил.
     */
    static void applyOrdered(World w, List<CommandBuffer> buffers) {
        buffers.sort((a, b) -> a.systemOrder != b.systemOrder
                ? Integer.compare(a.systemOrder, b.systemOrder)
                : Integer.compare(a.chunkStart, b.chunkStart));
        for (CommandBuffer cb : buffers) {
            for (int i = 0; i < cb.n; i++) {
                int t = cb.target[i];
                switch (cb.op[i]) {
                    case CommandBuffer.OP_ADD -> w.received[t] += cb.value[i];
                    case CommandBuffer.OP_SET -> w.received[t]  = cb.value[i];
                }
            }
        }
    }
}
