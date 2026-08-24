/** Deduplica una operación asíncrona compartida y limpia el slot sin que la
 * finalización de una generación anterior pueda borrar una más nueva. */
export function createSingleFlight<T>() {
  let inFlight: Promise<T> | null = null;

  return function run(start: () => Promise<T>): Promise<T> {
    if (!inFlight) {
      const current = Promise.resolve().then(start);
      inFlight = current;
      void current.then(
        () => {
          if (inFlight === current) inFlight = null;
        },
        () => {
          if (inFlight === current) inFlight = null;
        },
      );
    }
    return inFlight;
  };
}
