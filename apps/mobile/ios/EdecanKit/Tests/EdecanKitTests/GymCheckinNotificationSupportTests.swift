import Testing
@testable import EdecanKit

@Test func gymCheckinActionIdentifiersSonEstables() {
    #expect(GymCheckinNotificationSupport.categoriaIdentifier == "GYM_CHECKIN")
    #expect(GymCheckinNotificationSupport.accionSi == "GYM_YES")
    #expect(GymCheckinNotificationSupport.accionNo == "GYM_NO")
}

@Test func respuestaCheckinMapeaSiYNo() {
    #expect(GymCheckinNotificationSupport.respuestaCheckin(actionIdentifier: "GYM_YES") == "si")
    #expect(GymCheckinNotificationSupport.respuestaCheckin(actionIdentifier: "GYM_NO") == "no")
    #expect(GymCheckinNotificationSupport.respuestaCheckin(actionIdentifier: "GYM_SERIE") == nil)
    #expect(GymCheckinNotificationSupport.respuestaCheckin(actionIdentifier: "com.apple.UNNotificationDefaultActionIdentifier") == nil)
}

@Test func soloGymYesIniciaEntrenamiento() {
    #expect(GymCheckinNotificationSupport.debeIniciarEntrenamiento(actionIdentifier: "GYM_YES"))
    #expect(!GymCheckinNotificationSupport.debeIniciarEntrenamiento(actionIdentifier: "GYM_NO"))
    #expect(!GymCheckinNotificationSupport.debeIniciarEntrenamiento(actionIdentifier: "AVISO_HECHO"))
}

@Test func liveWorkoutUIVisibleConSesionActivaSinPlan() {
    #expect(
        GymCheckinNotificationSupport.mostrarLiveWorkoutUI(
            sesionActiva: true,
            pausada: false,
            entrenoActivo: false
        )
    )
    #expect(
        GymCheckinNotificationSupport.mostrarLiveWorkoutUI(
            sesionActiva: false,
            pausada: false,
            entrenoActivo: true
        )
    )
    #expect(
        !GymCheckinNotificationSupport.mostrarLiveWorkoutUI(
            sesionActiva: false,
            pausada: false,
            entrenoActivo: false
        )
    )
}

@Test func liveWorkoutUIVisibleEnDescanso() {
    #expect(
        GymCheckinNotificationSupport.mostrarLiveWorkoutUI(
            sesionActiva: false,
            pausada: false,
            entrenoActivo: false,
            descansoRestante: 45
        )
    )
}
