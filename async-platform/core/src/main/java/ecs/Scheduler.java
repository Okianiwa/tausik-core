package ecs;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Future;

/**
 * Из объявленных reads/writes И диапазонов архетипов строит стадии и исполняет их.
 * Тонкая гранулярность: две системы конфликтуют ТОЛЬКО если пересекаются и множества компонентов,
 * И их диапазоны строк. Системы разных архетипов с общим типом компонента, но disjoint-строками
 * попадают в одну стадию (возврат системного параллелизма — вывод среза 1).
 * Детерминизм — фикс. порядок систем + упорядоченный apply отсортированных буферов.
 */
public final class Scheduler {
    public final List<GameSystem> systems;
    public final int[] sysLo;
    public final int[] sysHi;
    public final List<int[]> stages; // стадия = массив индексов систем (в стабильном порядке)

    public Scheduler(List<GameSystem> systems, World w) {
        this.systems = systems;
        this.sysLo = new int[systems.size()];
        this.sysHi = new int[systems.size()];
        for (int i = 0; i < systems.size(); i++) {
            int a = systems.get(i).archetype();
            sysLo[i] = w.lo(a);
            sysHi[i] = w.hi(a);
        }
        this.stages = plan(systems, sysLo, sysHi);
    }

    private static boolean componentConflict(GameSystem a, GameSystem b) {
        return (a.writes() & b.writes()) != 0 || (a.writes() & b.reads()) != 0 || (a.reads() & b.writes()) != 0;
    }

    /** conflict(A,B) ⇔ пересечение компонентов И пересечение диапазонов строк. */
    public static List<int[]> plan(List<GameSystem> systems, int[] lo, int[] hi) {
        List<List<Integer>> stageSys = new ArrayList<>();
        for (int i = 0; i < systems.size(); i++) {
            int placed = -1;
            for (int st = 0; st < stageSys.size() && placed < 0; st++) {
                boolean conflict = false;
                for (int j : stageSys.get(st)) {
                    if (componentConflict(systems.get(i), systems.get(j))
                            && Archetype.rangeOverlap(lo[i], hi[i], lo[j], hi[j])) {
                        conflict = true;
                        break;
                    }
                }
                if (!conflict) placed = st;
            }
            if (placed < 0) { stageSys.add(new ArrayList<>()); placed = stageSys.size() - 1; }
            stageSys.get(placed).add(i);
        }
        List<int[]> out = new ArrayList<>();
        for (List<Integer> l : stageSys) {
            int[] a = new int[l.size()];
            for (int k = 0; k < a.length; k++) a[k] = l.get(k);
            out.add(a);
        }
        return out;
    }

    /** Однопоточный эталон: системы в порядке стадий, каждая — по своему диапазону строк. */
    public void runReference(World w) {
        for (int[] stage : stages) {
            List<CommandBuffer> buffers = new ArrayList<>();
            for (int si : stage) {
                GameSystem s = systems.get(si);
                View v = new View(w).bind(s.reads(), s.writes());
                CommandBuffer cb = new CommandBuffer(si, sysLo[si]);
                for (int e = sysLo[si]; e < sysHi[si]; e++) s.run(v, e, cb);
                buffers.add(cb);
            }
            applyOrdered(w, buffers);
        }
    }

    /** Параллельно: внутри стадии — независимые задачи (система × чанк её диапазона строк). */
    public void runParallel(World w, ExecutorService pool, int threads) throws Exception {
        int chunkCount = Math.max(1, threads);
        for (int[] stage : stages) {
            List<Callable<CommandBuffer>> tasks = new ArrayList<>();
            for (int si : stage) {
                final GameSystem s = systems.get(si);
                final int sysIdx = si;
                int[] bounds = chunkBounds(sysLo[si], sysHi[si], chunkCount);
                for (int c = 0; c < chunkCount; c++) {
                    final int lo = bounds[c], hi = bounds[c + 1];
                    if (lo == hi) continue;
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

    private static int[] chunkBounds(int lo, int hi, int chunks) {
        int n = hi - lo;
        int[] b = new int[chunks + 1];
        int base = n / chunks, rem = n % chunks, idx = lo;
        for (int i = 0; i < chunks; i++) { b[i] = idx; idx += base + (i < rem ? 1 : 0); }
        b[chunks] = hi;
        return b;
    }

    /**
     * Упорядоченный apply: буферы сортируются по (systemOrder, chunkStart), команды применяются
     * в порядке эмиссии. OP_SET на один таргет → last-writer по этому тотальному порядку.
     * Порядок не зависит от того, какой поток когда закончил → детерминизм.
     */
    static void applyOrdered(World w, List<CommandBuffer> buffers) {
        buffers.sort((a, b) -> a.systemOrder != b.systemOrder
                ? Integer.compare(a.systemOrder, b.systemOrder)
                : Integer.compare(a.chunkStart, b.chunkStart));
        for (CommandBuffer cb : buffers) {
            for (int i = 0; i < cb.n; i++) {
                int idx = w.invIndex(cb.entity[i], cb.slot[i]);
                switch (cb.op[i]) {
                    case CommandBuffer.OP_ADD -> w.inv[idx] += (int) cb.value[i];
                    case CommandBuffer.OP_SET -> w.inv[idx]  = (int) cb.value[i];
                }
            }
        }
    }
}
