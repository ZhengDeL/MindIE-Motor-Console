const MAX_INPUT_BYTES = 2 * 1024 * 1024;
const MAX_DIMENSION = 4096;
const MAX_PIXELS = 16_777_216;
const MAX_OUTPUT_BYTES = 256 * 1024;

function dimensions(raw: Uint8Array): { width: number; height: number; mime: string } {
  const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
  const ascii = (start: number, end: number) => String.fromCharCode(...raw.slice(start, end));
  if (raw.length >= 24 && raw.slice(0, 8).every((value, index) => value === [137, 80, 78, 71, 13, 10, 26, 10][index]) && ascii(12, 16) === "IHDR") {
    for (let offset = 8; offset + 12 <= raw.length;) {
      const length = view.getUint32(offset), type = ascii(offset + 4, offset + 8);
      if (type === "acTL") throw Error("请使用静态 PNG 图标，暂不支持动画。");
      if (length > raw.length - offset - 12) throw Error("PNG 文件不完整，请重新选择。");
      offset += length + 12;
    }
    return { width: view.getUint32(16), height: view.getUint32(20), mime: "image/png" };
  }
  if (raw.length >= 4 && raw[0] === 0xff && raw[1] === 0xd8) {
    let offset = 2;
    while (offset < raw.length) {
      if (raw[offset++] !== 0xff) break;
      while (offset < raw.length && raw[offset] === 0xff) offset++;
      const marker = raw[offset++];
      if (marker === 0xda || marker === 0xd9) break;
      if (marker === 0x01 || marker >= 0xd0 && marker <= 0xd7) continue;
      if (offset + 2 > raw.length) break;
      const length = view.getUint16(offset);
      if (length < 2 || offset + length > raw.length) break;
      if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker) && length >= 7)
        return { height: view.getUint16(offset + 3), width: view.getUint16(offset + 5), mime: "image/jpeg" };
      offset += length;
    }
    throw Error("JPEG 图像尺寸无法确认，请重新选择。");
  }
  if (raw.length >= 30 && ascii(0, 4) === "RIFF" && ascii(8, 12) === "WEBP") {
    const kind = ascii(12, 16);
    if (kind === "VP8X") {
      if (raw[20] & 2) throw Error("请使用静态 WebP 图标，暂不支持动画。");
      return { width: 1 + raw[24] + (raw[25] << 8) + (raw[26] << 16), height: 1 + raw[27] + (raw[28] << 8) + (raw[29] << 16), mime: "image/webp" };
    }
    if (kind === "VP8 " && raw[23] === 0x9d && raw[24] === 1 && raw[25] === 0x2a)
      return { width: view.getUint16(26, true) & 0x3fff, height: view.getUint16(28, true) & 0x3fff, mime: "image/webp" };
    if (kind === "VP8L" && raw[20] === 0x2f) {
      const bits = view.getUint32(21, true);
      return { width: 1 + (bits & 0x3fff), height: 1 + ((bits >>> 14) & 0x3fff), mime: "image/webp" };
    }
  }
  throw Error("请选择 PNG、JPEG 或静态 WebP 图像；不支持 SVG 和其他文件。");
}

/** Decode in the browser and upload a bounded canonical PNG, never arbitrary SVG or a remote URL. */
export async function prepareBrandIcon(file: File): Promise<string> {
  if (!file.size || file.size > MAX_INPUT_BYTES) throw Error("图标文件须大于 0 且不超过 2 MiB。");
  const raw = new Uint8Array(await file.arrayBuffer()), info = dimensions(raw);
  if (file.type && file.type !== info.mime) throw Error("文件类型与实际图像内容不一致，请重新选择。");
  if (!info.width || !info.height || info.width > MAX_DIMENSION || info.height > MAX_DIMENSION || info.width * info.height > MAX_PIXELS)
    throw Error("图像尺寸不能超过 4096 × 4096 像素，请先缩小图像。");
  let image: ImageBitmap;
  try { image = await createImageBitmap(file); }
  catch { throw Error("图像无法读取，请换用完整的 PNG、JPEG 或静态 WebP 文件。"); }
  try {
    if (!image.width || !image.height || image.width > MAX_DIMENSION || image.height > MAX_DIMENSION || image.width * image.height > MAX_PIXELS)
      throw Error("解码后的图像尺寸超出限制，请先缩小图像。");
    const canvas = document.createElement("canvas"); canvas.width = 256; canvas.height = 256;
    const context = canvas.getContext("2d"); if (!context) throw Error("当前浏览器无法处理图标，请更换浏览器后重试。");
    const ratio = Math.min(256 / image.width, 256 / image.height), width = image.width * ratio, height = image.height * ratio;
    context.clearRect(0, 0, 256, 256); context.drawImage(image, (256 - width) / 2, (256 - height) / 2, width, height);
    const result = canvas.toDataURL("image/png");
    if (!result.startsWith("data:image/png;base64,") || Math.ceil((result.length - 22) * 3 / 4) > MAX_OUTPUT_BYTES)
      throw Error("处理后的 PNG 超过 256 KiB，请使用更简洁的图标。");
    return result;
  } finally { image.close(); }
}
