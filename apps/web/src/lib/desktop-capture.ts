/** Captura foto o pantalla desde el navegador / WebView y la convierte en File. */

function canvasToFile(canvas: HTMLCanvasElement, filename: string): Promise<File> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error("No se pudo guardar la captura."));
        return;
      }
      resolve(new File([blob], filename, { type: "image/png" }));
    }, "image/png");
  });
}

function stamp(): string {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

export async function captureCameraPhoto(): Promise<File> {
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: "user", width: { ideal: 1280 }, height: { ideal: 720 } },
    audio: false,
  });
  try {
    const video = document.createElement("video");
    video.playsInline = true;
    video.muted = true;
    video.srcObject = stream;
    await video.play();
    await new Promise((resolve) => {
      if (video.readyState >= 2) {
        resolve(undefined);
        return;
      }
      video.onloadeddata = () => resolve(undefined);
    });
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth || 1280;
    canvas.height = video.videoHeight || 720;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("No se pudo dibujar la foto.");
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    return await canvasToFile(canvas, `foto-${stamp()}.png`);
  } finally {
    stream.getTracks().forEach((track) => track.stop());
  }
}

export async function captureDisplayFrame(): Promise<File> {
  const stream = await navigator.mediaDevices.getDisplayMedia({
    video: { frameRate: 1 },
    audio: false,
  });
  try {
    const video = document.createElement("video");
    video.playsInline = true;
    video.muted = true;
    video.srcObject = stream;
    await video.play();
    await new Promise((resolve) => {
      if (video.readyState >= 2) {
        resolve(undefined);
        return;
      }
      video.onloadeddata = () => resolve(undefined);
    });
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth || 1280;
    canvas.height = video.videoHeight || 720;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("No se pudo dibujar la captura.");
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    return await canvasToFile(canvas, `pantalla-${stamp()}.png`);
  } finally {
    stream.getTracks().forEach((track) => track.stop());
  }
}
