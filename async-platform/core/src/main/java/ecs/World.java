package ecs;

/**
 * Колоночное (SoA) хранилище блок-энтити. Каждый компонент — непрерывный массив
 * (cache-friendly, префетчится; важно для L3-резидентной плотной сцены на X3D).
 * Инвентарь хранится плоско entity-major (e*SLOTS+slot): три слота энтити рядом —
 * локальность, когда система трогает все слоты одной печи.
 */
public final class World {
    public final int size;

    public final int[]    inv;        // INVENTORY: size*SLOTS, entity-major
    public final long[]   energy;     // ENERGY
    public final int[]    progress;   // PROGRESS
    public final double[] heat;       // HEAT
    public final int[]    burnTime;   // FUEL
    public final int[]    recipeTicks;// RECIPE (read-only)
    public final int[]    link;       // LINK: цель хоппера (индекс энтити или -1)

    // Диапазоны строк по архетипу [lo,hi). Дефолт — весь мир; сцена задаёт disjoint-разбиение.
    public final int[]    archLo;
    public final int[]    archHi;

    public World(int size) {
        this.size = size;
        inv         = new int[size * Components.SLOTS];
        energy      = new long[size];
        progress    = new int[size];
        heat        = new double[size];
        burnTime    = new int[size];
        recipeTicks = new int[size];
        link        = new int[size];
        archLo = new int[Archetype.COUNT];
        archHi = new int[Archetype.COUNT];
        for (int a = 0; a < Archetype.COUNT; a++) { archLo[a] = 0; archHi[a] = size; }
    }

    public int lo(int arch) { return arch < 0 ? 0    : archLo[arch]; }
    public int hi(int arch) { return arch < 0 ? size : archHi[arch]; }

    public int invIndex(int entity, int slot) { return entity * Components.SLOTS + slot; }

    /** Детерминированный чек-сумм всего состояния — для диффа детерминизма ref vs parallel. */
    public long checksum() {
        long h = 1125899906842597L;
        for (int i = 0; i < inv.length; i++) h = 31 * h + inv[i];
        for (int i = 0; i < size; i++) {
            h = 31 * h + energy[i];
            h = 31 * h + progress[i];
            h = 31 * h + Double.doubleToLongBits(heat[i]);
            h = 31 * h + burnTime[i];
            h = 31 * h + recipeTicks[i];
            h = 31 * h + link[i];
        }
        return h;
    }

    public World copy() {
        World w = new World(size);
        System.arraycopy(inv,         0, w.inv,         0, inv.length);
        System.arraycopy(energy,      0, w.energy,      0, size);
        System.arraycopy(progress,    0, w.progress,    0, size);
        System.arraycopy(heat,        0, w.heat,        0, size);
        System.arraycopy(burnTime,    0, w.burnTime,    0, size);
        System.arraycopy(recipeTicks, 0, w.recipeTicks, 0, size);
        System.arraycopy(link,        0, w.link,        0, size);
        System.arraycopy(archLo,      0, w.archLo,      0, Archetype.COUNT);
        System.arraycopy(archHi,      0, w.archHi,      0, Archetype.COUNT);
        return w;
    }
}
