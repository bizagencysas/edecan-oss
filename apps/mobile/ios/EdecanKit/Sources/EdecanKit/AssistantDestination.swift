/// Las únicas superficies de primer nivel de Edecan. Las capacidades
/// especializadas viven dentro de una de ellas, no como productos separados.
public enum AssistantDestination: String, CaseIterable, Sendable {
    case edecan
    case activity
    case settings
}
