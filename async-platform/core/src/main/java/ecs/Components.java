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
    public static final int BUSY      = 13;  // scratch-приёмник диагностической work (вне checksum)
    public static final int COUNT     = 14;

    // Слоты инвентаря блок-энтити (entity-major раскладка: e*SLOTS + slot).
    public static final int SLOT_INPUT  = 0;
    public static final int SLOT_FUEL   = 1;
    public static final int SLOT_OUTPUT = 2;
    public static final int SLOTS       = 3;

    // Дорожки многоарных компонентов (POSITION/VELOCITY хранят x,y в одной колонке).
    public static final int LANE_X = 0;
    public static final int LANE_Y = 1;

    /**
     * Реестр стандартных компонентов. Порядок register() ОБЯЗАН совпадать с константами выше —
     * сверяется здесь же: рассинхрон уронит старт, а не сместит молча колонки в хранилище.
     */
    public static ComponentRegistry standard() {
        ComponentRegistry r = new ComponentRegistry();
        expect(r.register("INVENTORY",  ComponentRegistry.Kind.INT,    SLOTS), INVENTORY);
        expect(r.register("ENERGY",     ComponentRegistry.Kind.LONG,   1), ENERGY);
        expect(r.register("PROGRESS",   ComponentRegistry.Kind.INT,    1), PROGRESS);
        expect(r.register("HEAT",       ComponentRegistry.Kind.DOUBLE, 1), HEAT);
        expect(r.register("FUEL",       ComponentRegistry.Kind.INT,    1), FUEL);
        expect(r.register("RECIPE",     ComponentRegistry.Kind.INT,    1), RECIPE);
        expect(r.register("LINK",       ComponentRegistry.Kind.INT,    1), LINK);
        expect(r.register("POSITION",   ComponentRegistry.Kind.DOUBLE, 2), POSITION);
        expect(r.register("VELOCITY",   ComponentRegistry.Kind.DOUBLE, 2), VELOCITY);
        expect(r.register("HEALTH",     ComponentRegistry.Kind.INT,    1), HEALTH);
        expect(r.register("POWER",      ComponentRegistry.Kind.INT,    1), POWER);
        // POWER_NEXT — буфер записи каскада, не состояние мира → вне checksum (как в старом World).
        expect(r.registerScratch("POWER_NEXT", ComponentRegistry.Kind.INT, 1), POWER_NEXT);
        expect(r.register("SOURCE",     ComponentRegistry.Kind.INT,    1), SOURCE);
        expect(r.registerScratch("BUSY", ComponentRegistry.Kind.LONG,  1), BUSY);
        if (r.count() != COUNT) throw new IllegalStateException("count=" + r.count() + " != COUNT=" + COUNT);
        return r;
    }

    private static void expect(int got, int want) {
        if (got != want)
            throw new IllegalStateException("Порядок регистрации разошёлся с константой: id=" + got + ", ожидался " + want);
    }

    public static long bit(int comp) { return 1L << comp; }
    public static long mask(int... comps) {
        long m = 0;
        for (int c : comps) m |= bit(c);
        return m;
    }

    private Components() {}
}
