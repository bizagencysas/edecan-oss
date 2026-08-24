import Foundation
import Testing
@testable import EdecanKit

struct PairingLinkTests {
    @Test func politicaAceptaIPv4PrivadaPorHTTP() throws {
        let url = try #require(URL(string: "http://192.168.58.105:8000"))
        #expect(try ServerURLPolicy.validate(url) == url)
    }

    @Test func enlaceQRAceptaIPv4PrivadaPorHTTP() throws {
        var components = URLComponents()
        components.scheme = "edecan"
        components.host = "pair"
        components.queryItems = [
            URLQueryItem(name: "server", value: "http://192.168.58.105:8000"),
            URLQueryItem(name: "token", value: "opaque-local-token"),
        ]

        let link = try PairingLink(url: try #require(components.url))

        #expect(link.serverURL.absoluteString == "http://192.168.58.105:8000")
    }

    @Test func politicaRechazaHostPublicoPorHTTP() throws {
        let url = try #require(URL(string: "http://evil.example"))
        #expect(throws: ServerURLPolicy.ValidationError.insecureRemoteHTTP) {
            try ServerURLPolicy.validate(url)
        }
    }

    @Test func politicaAceptaHostPublicoPorHTTPS() throws {
        let url = try #require(URL(string: "https://api.edecan.example/base"))
        #expect(try ServerURLPolicy.validate(url) == url)
    }

    @Test func politicaAceptaNombresEIPv6LocalesYRechazaParametros() throws {
        for raw in ["http://localhost:8000", "http://edecan:8000", "http://edecan.local", "http://[::1]:8000", "http://[fd12:3456::1]"] {
            let url = try #require(URL(string: raw))
            #expect(try ServerURLPolicy.validate(url) == url)
        }
        for raw in ["https://api.edecan.example?token=x", "https://api.edecan.example#secreto", "https://user:pass@api.edecan.example"] {
            let url = try #require(URL(string: raw))
            #expect(throws: ServerURLPolicy.ValidationError.embeddedCredentialsOrParameters) {
                try ServerURLPolicy.validate(url)
            }
        }
    }

    @Test func decodificaServerUrlEncodedYConservaTokenOpaco() throws {
        var components = URLComponents()
        components.scheme = "edecan"
        components.host = "pair"
        components.queryItems = [
            URLQueryItem(name: "server", value: "https://asistente.example.com:8443/base"),
            URLQueryItem(name: "token", value: "opaque+token/with=padding"),
        ]
        let link = try PairingLink(url: try #require(components.url))

        #expect(link.serverURL.absoluteString == "https://asistente.example.com:8443/base")
        #expect(link.token == "opaque+token/with=padding")
    }

    @Test func rechazaEsquemaRutaCredencialesYParametrosDuplicados() throws {
        #expect(throws: PairingLink.ParseError.self) {
            try PairingLink(url: #require(URL(string: "https://pair?server=https%3A%2F%2Fa.test&token=x")))
        }
        #expect(throws: PairingLink.ParseError.self) {
            try PairingLink(url: #require(URL(string: "edecan://otra?server=https%3A%2F%2Fa.test&token=x")))
        }
        #expect(throws: PairingLink.ParseError.self) {
            try PairingLink(url: #require(URL(string: "edecan://pair?server=https%3A%2F%2Fuser%3Apass%40a.test&token=x")))
        }
        #expect(throws: PairingLink.ParseError.self) {
            try PairingLink(url: #require(URL(string: "edecan://pair?server=https%3A%2F%2Fa.test&server=https%3A%2F%2Fb.test&token=x")))
        }
    }

    @Test func rechazaTokenAusenteOConControl() throws {
        #expect(throws: PairingLink.ParseError.self) {
            try PairingLink(url: #require(URL(string: "edecan://pair?server=https%3A%2F%2Fa.test")))
        }
        var components = URLComponents()
        components.scheme = "edecan"
        components.host = "pair"
        components.queryItems = [
            URLQueryItem(name: "server", value: "https://a.test"),
            URLQueryItem(name: "token", value: "token\ninyectado"),
        ]
        #expect(throws: PairingLink.ParseError.self) {
            try PairingLink(url: #require(components.url))
        }
    }
}
