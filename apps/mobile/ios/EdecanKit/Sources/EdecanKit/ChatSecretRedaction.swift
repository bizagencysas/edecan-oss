import Foundation

/// Redacción local para que una credencial nunca se pinte en una burbuja
/// optimista mientras el request viaja al Edecán maestro.
public enum ChatSecretRedaction {
    private static let patterns: [NSRegularExpression] = [
        #"\bsk[-_][A-Za-z0-9_-]{8,}"#,
        #"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"#,
        #"\b(?:rk_live|rk_test|whsec)_[A-Za-z0-9]{8,}"#,
        #"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"#,
    ].compactMap { try? NSRegularExpression(pattern: $0, options: [.caseInsensitive]) }

    public static func redact(_ text: String) -> String {
        patterns.reduce(text) { current, regex in
            let range = NSRange(current.startIndex..<current.endIndex, in: current)
            return regex.stringByReplacingMatches(
                in: current,
                range: range,
                withTemplate: "[credencial protegida]"
            )
        }
    }
}
