package ecs;

/**
 * Архетип = класс энтити с одинаковым набором компонентов, занимающий непрерывный диапазон строк.
 * Тонкая гранулярность конфликта: системы разных архетипов НЕ конфликтуют, даже если пишут один
 * тип компонента — их строки не пересекаются. UNIVERSAL покрывает весь мир (старое поведение).
 */
public final class Archetype {
    public static final int UNIVERSAL = -1;
    public static final int FURNACE = 0;
    public static final int MACHINE = 1;
    public static final int HOPPER  = 2;
    public static final int MOB     = 3;   // сущности (грязные подсистемы)
    public static final int COUNT   = 4;

    /** Полуоткрытые диапазоны [lo,hi) пересекаются? Пустой диапазон (lo==hi) не пересекается ни с чем. */
    public static boolean rangeOverlap(int loA, int hiA, int loB, int hiB) {
        return loA < hiB && loB < hiA;
    }

    private Archetype() {}
}
