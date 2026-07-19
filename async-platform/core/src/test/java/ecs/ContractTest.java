package ecs;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;

class ContractTest {

    private static final long MASK = Components.mask(Components.PROGRESS, Components.ENERGY);

    private static ArchetypeWorld world() { return BoundaryTest.world(MASK, 4); }

    /** Объявляет только PROGRESS, но пытается писать ENERGY — контракт обязан бросить. */
    private static final class RogueSystem implements GameSystem {
        public String name() { return "Rogue"; }
        public long reads()  { return 0; }
        public long writes() { return Components.bit(Components.PROGRESS); }
        public void run(View v, int e, CommandBuffer cb) { v.setLong(Components.ENERGY, e, 1); }
    }

    /** Читает необъявленный компонент. */
    private static final class PeekSystem implements GameSystem {
        public String name() { return "Peek"; }
        public long reads()  { return Components.bit(Components.PROGRESS); }
        public long writes() { return 0; }
        public void run(View v, int e, CommandBuffer cb) { v.getLong(Components.ENERGY, e); }
    }

    @Test
    void undeclaredWriteThrows() {
        ArchetypeWorld w = world();
        Scheduler s = new Scheduler(List.of(new RogueSystem()), w);
        assertThrows(ContractViolation.class, () -> s.runReference(w));
    }

    @Test
    void undeclaredReadThrows() {
        ArchetypeWorld w = world();
        Scheduler s = new Scheduler(List.of(new PeekSystem()), w);
        assertThrows(ContractViolation.class, () -> s.runReference(w));
    }

    @Test
    void declaredAccessIsFine() {
        GameSystem ok = new GameSystem() {
            public String name() { return "Ok"; }
            public long reads()  { return 0; }
            public long writes() { return Components.bit(Components.PROGRESS); }
            public void run(View v, int e, CommandBuffer cb) {
                v.setInt(Components.PROGRESS, e, v.getInt(Components.PROGRESS, e) + 1);
            }
        };
        ArchetypeWorld w = world();
        Scheduler s = new Scheduler(List.of(ok), w);
        assertDoesNotThrow(() -> s.runReference(w));
    }
}
