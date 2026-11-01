/** Unicode码点序：与UTF-8字节序及SQLite BINARY一致，不读取ICU/locale。
 *  前提：a、b为良构UTF-16（String.prototype.isWellFormed()为true）。孤立代理单元会按其码元值参与比较，
 *  而Buffer/napi_get_value_string_utf8会把它替换为U+FFFD，二者此时可能不一致。
 */
export function compareCodePoints(a: string, b: string): number {
    const n = Math.min(a.length, b.length);
    for (let i = 0; i < n; i++) {
        const x = a.charCodeAt(i), y = b.charCodeAt(i);
        if (x !== y)
            return fold(x) - fold(y);
    }
    return a.length - b.length;
}
/** 代理单元表示U+10000以上码点，须排在U+E000–U+FFFF之后。 */
function fold(unit: number): number {
    return unit >= 0xE000 ? unit - 0x800 : unit >= 0xD800 ? unit + 0x2000 : unit;
}
