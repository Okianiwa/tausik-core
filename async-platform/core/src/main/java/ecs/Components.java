package ecs;

/**
 * Идентификаторы компонентов = индексы битов в масках reads/writes.
 * Гранулярность конфликта — ТИП компонента (contract §2, консервативно).
 */
public final class Components {
    public static final int INVENTORY = 0;
    public static final int ENERGY    = 1;
    public static final int PROGRESS  = 2;
    public static final int HEAT      = 3;
    public static final int FUEL      = 4;   // burnTime
    public static final int RECIPE    = 5;   // read-only константа тика плавки
    public static final int LINK      = 6;   // read-only цель хоппера
    public static final int POSITION  = 7;   // сущности: posX/posY
    public static final int VELOCITY  = 8;   // сущности: velX/velY
    public static final int HEALTH    = 9;   // сущности
    public static final int POWER     = 10;  // редстоун: стабильный (read) буфер
    public static final int POWER_NEXT= 11;  // редстоун: буфер записи (double-buffer каскада)
    public static final int SOURCE    = 12;  // редстоун: клетка-источник (read-only)
    public static final int COUNT     = 13;

    // Слоты инвентаря блок-энтити (entity-major раскладка: e*SLOTS + slot).
    public static final int SLOT_INPUT  = 0;
    public static final int SLOT_FUEL   = 1;
    public static final int SLOT_OUTPUT = 2;
    public static final int SLOTS       = 3;

    public static long bit(int comp) { return 1L << comp; }
    public static long mask(int... comps) {
        long m = 0;
        for (int c : comps) m |= bit(c);
        return m;
    }

    private Components() {}
}
