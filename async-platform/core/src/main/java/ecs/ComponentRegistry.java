package ecs;

/**
 * Реестр типов компонентов: id → (kind, arity). Снимает с World знание о конкретных колонках —
 * хранилище аллоцирует колонку ПО ОПИСАНИЮ из реестра, а не по захардкоженному полю.
 * Добавление компонента = один register(...) в месте декларации, без правки World/View/checksum.
 */
public final class ComponentRegistry {
    /**
     * Потолок = 64: маски reads/writes — long (Components.bit сдвигает 1L << comp).
     * Молча терять биты при 65-м компоненте недопустимо — падаем громко здесь.
     */
    public static final int MAX_COMPONENTS = 64;

    public enum Kind { INT, LONG, DOUBLE }

    private final String[] names = new String[MAX_COMPONENTS];
    private final Kind[] kinds = new Kind[MAX_COMPONENTS];
    private final int[] arities = new int[MAX_COMPONENTS];
    private final boolean[] checksummed = new boolean[MAX_COMPONENTS];
    private int count;

    /**
     * Компонент вне checksum: scratch-буферы, чьё значение не является состоянием мира
     * (POWER_NEXT — буфер записи каскада, BUSY — приёмник диагностической work).
     * Флаг живёт ЗДЕСЬ, а не в World: иначе World снова знал бы компоненты поимённо и AC #2
     * (добавление компонента без правки World) был бы нарушен.
     */
    public int registerScratch(String name, Kind kind, int arity) {
        int id = register(name, kind, arity);
        checksummed[id] = false;
        return id;
    }

    public boolean inChecksum(int id) { check(id); return checksummed[id]; }

    /** Возвращает id нового компонента. Порядок регистрации = порядок id. */
    public int register(String name, Kind kind, int arity) {
        if (name == null || name.isEmpty()) throw new IllegalArgumentException("Пустое имя компонента");
        if (kind == null) throw new IllegalArgumentException("kind == null для '" + name + "'");
        if (arity < 1) throw new IllegalArgumentException("arity=" + arity + " < 1 для '" + name + "'");
        if (count >= MAX_COMPONENTS) {
            throw new IllegalStateException(
                    "Потолок " + MAX_COMPONENTS + " компонентов достигнут — маска reads/writes это long ("
                            + "Components.bit = 1L << comp). Компонент '" + name + "' не влезает. "
                            + "Расширение маски (long[2] / BitSet) — отдельная задача, не молчаливый оверфлоу.");
        }
        int id = count++;
        names[id] = name;
        kinds[id] = kind;
        arities[id] = arity;
        checksummed[id] = true;
        return id;
    }

    public int count() { return count; }
    public String name(int id) { check(id); return names[id]; }
    public Kind kind(int id) { check(id); return kinds[id]; }
    public int arity(int id) { check(id); return arities[id]; }

    private void check(int id) {
        if (id < 0 || id >= count)
            throw new IllegalArgumentException("Компонент " + id + " не зарегистрирован (count=" + count + ")");
    }
}
