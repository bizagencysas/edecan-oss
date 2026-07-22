//! Decisiones puras del ciclo de vida de las ventanas.
//!
//! La ventana principal se comporta como la de un asistente residente:
//! cerrarla la oculta, pero no apaga el backend ni las tareas en segundo
//! plano. Las ventanas auxiliares (hoy solo el splash de arranque) conservan
//! el cierre completo para no dejar una app fallida sin interfaz visible.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum WindowCloseAction {
    Hide,
    Exit,
}

pub(crate) fn close_action(window_label: &str, keep_resident: bool) -> WindowCloseAction {
    if window_label == "main" && keep_resident {
        WindowCloseAction::Hide
    } else {
        WindowCloseAction::Exit
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn closing_main_hides_the_resident_assistant() {
        assert_eq!(close_action("main", true), WindowCloseAction::Hide);
    }

    #[test]
    fn non_resident_or_auxiliary_windows_exit_cleanly() {
        assert_eq!(close_action("main", false), WindowCloseAction::Exit);
        assert_eq!(close_action("splash", true), WindowCloseAction::Exit);
        assert_eq!(close_action("unexpected", true), WindowCloseAction::Exit);
    }
}
