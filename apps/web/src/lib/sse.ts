function dataFromFrame(frame: string): string | null {
  const dataLines: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line === "data") {
      dataLines.push("");
      continue;
    }
    if (!line.startsWith("data:")) continue;
    const value = line.slice(5);
    dataLines.push(value.startsWith(" ") ? value.slice(1) : value);
  }
  return dataLines.length > 0 ? dataLines.join("\n") : null;
}

/** Parser incremental SSE: tolera LF/CRLF, chunks arbitrarios y flush sin línea vacía final. */
export class SseDataParser {
  private buffer = "";

  push(chunk: string, endOfStream = false): string[] {
    this.buffer += chunk;
    const payloads: string[] = [];

    for (;;) {
      const separator = this.buffer.match(/\r?\n\r?\n/);
      if (!separator || separator.index === undefined) break;
      const frame = this.buffer.slice(0, separator.index);
      this.buffer = this.buffer.slice(separator.index + separator[0].length);
      const data = dataFromFrame(frame);
      if (data !== null) payloads.push(data);
    }

    if (endOfStream && this.buffer.length > 0) {
      const data = dataFromFrame(this.buffer);
      if (data !== null) payloads.push(data);
      this.buffer = "";
    }
    return payloads;
  }
}
