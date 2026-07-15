package ecs;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Самопроверяющийся прогон Phase 0: бенч speedup + диф детерминизма + негативные/граничные кейсы.
 * Возвращает exit code 1, если любая проверка провалилась.
 * Сцена = «плотная база»: N однотипных «машин»-энтити под немногими системами (случай, где
 * регионы бессильны, а ECS даёт data-parallel по строкам).
 */
public final class Demo {

    // Параметры сцены/бенча (7800X3D: потолок настоящего масштабирования = 8 физ. ядер)
    static final int N = 150_000;
    static final int WORK = 24;
    static final int WARMUP = 10;
    static final int MEASURE = 30;
    static final int[] THREADS = {1, 2, 4, 8, 16};

    // ---- Компьют-представительная детерминированная мешалка (целочисленная → точно воспроизводима) ----
    static long mix(long x, int work) {
        for (int k = 0; k < work; k++) {
            x ^= x >>> 13;
            x *= 0x9E3779B97F4A7C15L;
            x += (k * 0x100000001B3L);
        }
        return x;
    }

    // ---- Системы плотной сцены ----
    static final class Thermal implements GameSystem {
        public String name() { return "Thermal"; }
        public long reads()  { return World.bit(World.POS); }
        public long writes() { return World.bit(World.HEAT); }
        public void run(View v, int e, CommandBuffer cb) {
            long m = mix((long) v.posX(e) * 2654435761L + v.posY(e) * 40499L + v.posZ(e) + 0xAAAAL, WORK);
            v.setHeat(e, (double) (m & 0xFFFFFFL));
        }
    }
    static final class Energy implements GameSystem {
        public String name() { return "Energy"; }
        public long reads()  { return World.bit(World.POS); }
        public long writes() { return World.bit(World.ENERGY); }
        public void run(View v, int e, CommandBuffer cb) {
            v.setEnergy(e, mix((long) v.posX(e) * 40503L ^ 0xBBBBL, WORK));
        }
    }
    static final class Progress implements GameSystem {
        final int n; Progress(int n){ this.n = n; }
        public String name() { return "Progress"; }
        public long reads()  { return World.bit(World.POS); }
        public long writes() { return World.bit(World.PROGRESS); }
        public void run(View v, int e, CommandBuffer cb) {
            long m = mix((long) v.posX(e) + v.posY(e) * 7L + 0xCCCCL, WORK);
            int prog = (int) (m & 0x7FFFFFFF);
            v.setProgress(e, prog);
            cb.set((e + 1) % n, prog);          // OP_SET → порядко-зависимая (тест детерминизма apply)
        }
    }
    static final class Inventory implements GameSystem {
        final int n; Inventory(int n){ this.n = n; }
        public String name() { return "Inventory"; }
        public long reads()  { return World.bit(World.POS); }
        public long writes() { return World.bit(World.INV); }
        public void run(View v, int e, CommandBuffer cb) {
            long inv = mix((long) v.posX(e) * 2246822519L + 0xDDDDL, WORK) & 0xFFFF;
            v.setInv(e, inv);
            cb.add((e + 1) % n, (inv & 15) + 1);  // OP_ADD → коммутативная
        }
    }
    static final class Smelt implements GameSystem {          // stage 1: зависит от ENERGY/HEAT из stage 0
        public String name() { return "Smelt"; }
        public long reads()  { return World.bit(World.ENERGY) | World.bit(World.HEAT); }
        public long writes() { return World.bit(World.PROGRESS); }
        public void run(View v, int e, CommandBuffer cb) {
            long m = mix(v.energy(e) ^ (long) v.heat(e) ^ 0xEEEEL, WORK);
            v.setProgress(e, (int) (m & 0x7FFFFFFF));
        }
    }

    static List<GameSystem> benchSystems(int n) {
        List<GameSystem> s = new ArrayList<>();
        s.add(new Thermal()); s.add(new Energy()); s.add(new Progress(n));
        s.add(new Inventory(n)); s.add(new Smelt());
        return s;
    }

    static World scene(int n, long seed) {
        World w = new World(n);
        for (int i = 0; i < n; i++) {
            w.posX[i] = (int) ((i * 13L + seed) & 0xFFFF);
            w.posY[i] = (int) ((i * 7L + seed * 3) & 0xFFFF);
            w.posZ[i] = (int) ((i * 5L + seed * 11) & 0xFFFF);
        }
        return w;
    }

    static long median(long[] a) { long[] c = a.clone(); Arrays.sort(c); return c[c.length / 2]; }

    public static void main(String[] args) throws Exception {
        boolean ok = true;
        System.out.println("=== Phase 0 ECS prototype — self-check ===");
        System.out.println("cores(logical)=" + Runtime.getRuntime().availableProcessors()
                + "  N=" + N + "  WORK=" + WORK + "  warmup=" + WARMUP + "  measure=" + MEASURE);

        Scheduler sch = new Scheduler(benchSystems(N));
        System.out.println("\n[schedule] stages=" + sch.stages.size());
        for (int i = 0; i < sch.stages.size(); i++) {
            StringBuilder sb = new StringBuilder("  stage " + i + ": ");
            for (int si : sch.stages.get(i)) sb.append(sch.systems.get(si).name()).append(" ");
            System.out.println(sb);
        }

        // ---- Диф детерминизма: reference vs parallel(8) ----
        System.out.println("\n[determinism] reference vs parallel(8), " + MEASURE + " ticks");
        World wr = scene(N, 42);
        for (int t = 0; t < MEASURE; t++) sch.runReference(wr);
        long refSum = wr.checksum();
        World wp = scene(N, 42);
        try (Pool pool = new Pool(8)) {
            for (int t = 0; t < MEASURE; t++) sch.runParallel(wp, pool.svc, 8);
        }
        long parSum = wp.checksum();
        boolean detOk = refSum == parSum;
        System.out.printf("  ref=%d par=%d  -> %s%n", refSum, parSum, detOk ? "IDENTICAL ✓" : "DIVERGED ✗");
        ok &= detOk;

        // ---- Бенч speedup ----
        System.out.println("\n[benchmark] median tick (ms), speedup vs reference & vs parallel@1");
        World wref = scene(N, 7);
        for (int t = 0; t < WARMUP; t++) sch.runReference(wref);
        long[] refT = new long[MEASURE];
        for (int t = 0; t < MEASURE; t++) { long s = System.nanoTime(); sch.runReference(wref); refT[t] = System.nanoTime() - s; }
        double refMs = median(refT) / 1e6;
        System.out.printf("  reference(no-pool): %.2f ms%n", refMs);

        double par1Ms = -1;
        for (int th : THREADS) {
            World w = scene(N, 7);
            try (Pool pool = new Pool(th)) {
                for (int t = 0; t < WARMUP; t++) sch.runParallel(w, pool.svc, th);
                long[] tt = new long[MEASURE];
                for (int t = 0; t < MEASURE; t++) { long s = System.nanoTime(); sch.runParallel(w, pool.svc, th); tt[t] = System.nanoTime() - s; }
                double ms = median(tt) / 1e6;
                if (th == 1) par1Ms = ms;
                System.out.printf("  parallel@%-2d : %6.2f ms   speedup(vs ref)=%.2fx   speedup(vs @1)=%.2fx%n",
                        th, ms, refMs / ms, par1Ms / ms);
            }
        }
        // Оценка серийной доли по Амдалу из speedup@8 (vs @1)
        // (печатается сноской; выводы — в report)

        // ---- Негатив #1: write-write конфликт разносится по стадиям (сериализация) ----
        System.out.println("\n[negative: conflict] два писателя ENERGY должны попасть в РАЗНЫЕ стадии");
        List<GameSystem> two = new ArrayList<>();
        two.add(new Energy()); two.add(new Energy());
        List<int[]> pl = Scheduler.plan(two);
        boolean serialized = pl.size() == 2;
        System.out.println("  stages=" + pl.size() + " -> " + (serialized ? "SERIALIZED ✓" : "RACE-RISK ✗"));
        ok &= serialized;

        // ---- Негатив #2: write-write через OP_SET разрешается детерминированно (last по systemOrder) ----
        System.out.println("\n[negative: set-resolution] later systemOrder wins, стабильно на 8 потоках");
        boolean setOk = true;
        for (int rep = 0; rep < 200 && setOk; rep++) {
            World w = new World(1);
            List<GameSystem> conf = new ArrayList<>();
            conf.add(new Setter(111L)); // systemOrder 0
            conf.add(new Setter(222L)); // systemOrder 1 -> должен победить
            Scheduler cs = new Scheduler(conf);
            try (Pool pool = new Pool(8)) { cs.runParallel(w, pool.svc, 8); }
            if (w.received[0] != 222L) setOk = false;
        }
        System.out.println("  received[0]==222 across 200 reps -> " + (setOk ? "DETERMINISTIC ✓" : "NONDET ✗"));
        ok &= setOk;

        // ---- Негатив #3: обращение к необъявленному компоненту -> ContractViolation ----
        System.out.println("\n[negative: contract] необъявленная запись должна бросить ContractViolation");
        boolean caught = false;
        try {
            World w = new World(1);
            GameSystem buggy = new GameSystem() {
                public String name() { return "Buggy"; }
                public long reads() { return World.bit(World.POS); }
                public long writes() { return World.bit(World.PROGRESS); } // объявил PROGRESS
                public void run(View v, int e, CommandBuffer cb) { v.setEnergy(e, 1); } // а пишет ENERGY
            };
            new View(w).bind(buggy.reads(), buggy.writes());
            buggy.run(new View(w).bind(buggy.reads(), buggy.writes()), 0, new CommandBuffer(0, 0));
        } catch (ContractViolation cv) { caught = true; System.out.println("  поймано: " + cv.getMessage()); }
        System.out.println("  -> " + (caught ? "DETECTED ✓" : "SWALLOWED ✗"));
        ok &= caught;

        // ---- Граница: пустая сцена / pure-read система / пустой command-buffer ----
        System.out.println("\n[boundary] N=0, pure-read система, пустой command-buffer");
        boolean bnd = true;
        try {
            World w0 = new World(0);                              // N=0
            try (Pool pool = new Pool(8)) { sch.runParallel(w0, pool.svc, 8); }
            List<GameSystem> pr = new ArrayList<>();
            pr.add(new GameSystem() {                             // writes=0 (pure read), эмитит 0 команд
                public String name() { return "PureRead"; }
                public long reads() { return World.bit(World.POS); }
                public long writes() { return 0; }
                public void run(View v, int e, CommandBuffer cb) { if (v.posX(e) < 0) throw new IllegalStateException(); }
            });
            World w1 = scene(1000, 1);
            Scheduler prs = new Scheduler(pr);
            try (Pool pool = new Pool(8)) { prs.runParallel(w1, pool.svc, 8); }  // apply над пустыми буферами
        } catch (Throwable th) { bnd = false; System.out.println("  упало: " + th); }
        System.out.println("  -> " + (bnd ? "NO CRASH ✓" : "CRASHED ✗"));
        ok &= bnd;

        System.out.println("\n=== RESULT: " + (ok ? "ALL CHECKS PASSED ✓" : "FAILURES ✗") + " ===");
        System.exit(ok ? 0 : 1);
    }

    // Сеттер для теста разрешения write-write: пишет received[0] через OP_SET
    static final class Setter implements GameSystem {
        final long val; Setter(long v){ this.val = v; }
        public String name() { return "Setter"; }
        public long reads() { return 0; }
        public long writes() { return 0; }
        public void run(View v, int e, CommandBuffer cb) { cb.set(0, val); }
    }

    // AutoCloseable-обёртка пула, чтобы гарантированно гасить потоки
    static final class Pool implements AutoCloseable {
        final ExecutorService svc;
        Pool(int threads) { svc = Executors.newFixedThreadPool(threads); }
        public void close() { svc.shutdownNow(); }
    }
}
