package ecs;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;

class ContractTest {

    /** Объявляет только PROGRESS, но пытается писать ENERGY — контракт обязан бросить. */
    private static final class RogueSystem implements GameSystem {
        public String name() { return "Rogue"; }
        public long reads()  { return 0; }
        public long writes() { return Components.bit(Components.PROGRESS); }
        public void run(View v, int e, CommandBuffer cb) { v.setEnergy(e, 1); }
    }

    /** Читает необъявленный компонент. */
    private static final class PeekSystem implements GameSystem {
        public String name() { return "Peek"; }
        public long reads()  { return Components.bit(Components.PROGRESS); }
        public long writes() { return 0; }
        public void run(View v, int e, CommandBuffer cb) { v.energy(e); }
    }

    @Test
    void undeclaredWriteThrows() {
        World w = new World(4);
        Scheduler s = new Scheduler(List.of(new RogueSystem()), w);
        assertThrows(ContractViolation.class, () -> s.runReference(w));
    }

    @Test
    void undeclaredReadThrows() {
        World w = new World(4);
        Scheduler s = new Scheduler(List.of(new PeekSystem()), w);
        assertThrows(ContractViolation.class, () -> s.runReference(w));
    }

    @Test
    void declaredAccessIsFine() {
        GameSystem ok = new GameSystem() {
            public String name() { return "Ok"; }
            public long reads()  { return 0; }
            public long writes() { return Components.bit(Components.PROGRESS); }
            public void run(View v, int e, CommandBuffer cb) { v.setProgress(e, v.progress(e) + 1); }
        };
        World w = new World(4);
        Scheduler s = new Scheduler(List.of(ok), w);
        assertDoesNotThrow(() -> s.runReference(w));
    }
}
