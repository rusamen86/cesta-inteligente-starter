import AppKit
import Foundation
import PDFKit
import Vision

enum CestaOCRError: Error {
    case invalidArguments
    case unreadableInput
    case imageConversionFailed
    case noText
}

struct PageResult {
    let text: String
    let rightColumnText: String
    let confidences: [Float]
}

struct RecognizedToken {
    let text: String
    let confidence: Float
    let box: CGRect
}

func recognizeTokens(_ cgImage: CGImage, xOffset: CGFloat = 0, xScale: CGFloat = 1) throws -> [RecognizedToken] {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["es-ES"]
    request.usesLanguageCorrection = true

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try handler.perform([request])
    var tokens: [RecognizedToken] = []
    for observation in request.results ?? [] {
        guard let candidate = observation.topCandidates(1).first else { continue }
        var box = observation.boundingBox
        box.origin.x = xOffset + box.origin.x * xScale
        box.size.width *= xScale
        tokens.append(RecognizedToken(text: candidate.string, confidence: candidate.confidence, box: box))
    }
    return tokens
}

func recognize(_ cgImage: CGImage) throws -> PageResult {
    let tokens = try recognizeTokens(cgImage)
    let cropFraction: CGFloat = 0.72
    let cropX = Int(CGFloat(cgImage.width) * cropFraction)
    let cropRect = CGRect(x: cropX, y: 0, width: cgImage.width - cropX, height: cgImage.height)
    let rightTokens: [RecognizedToken]
    if let rightColumn = cgImage.cropping(to: cropRect) {
        rightTokens = try recognizeTokens(rightColumn, xOffset: cropFraction, xScale: 1 - cropFraction)
    } else {
        rightTokens = []
    }
    let ordered = tokens.sorted {
        let verticalDelta = $0.box.maxY - $1.box.maxY
        return abs(verticalDelta) > 0.01 ? verticalDelta > 0 : $0.box.minX < $1.box.minX
    }
    let orderedRight = rightTokens.sorted {
        let verticalDelta = $0.box.maxY - $1.box.maxY
        return abs(verticalDelta) > 0.01 ? verticalDelta > 0 : $0.box.minX < $1.box.minX
    }
    return PageResult(
        text: ordered.map(\.text).joined(separator: "\n"),
        rightColumnText: orderedRight.map(\.text).joined(separator: "\n"),
        confidences: (tokens + rightTokens).map(\.confidence)
    )
}

func cgImage(from image: NSImage) throws -> CGImage {
    var rect = CGRect(origin: .zero, size: image.size)
    guard let result = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else {
        throw CestaOCRError.imageConversionFailed
    }
    return result
}

func extractPages(from url: URL) throws -> [PageResult] {
    if url.pathExtension.lowercased() == "pdf" {
        guard let document = PDFDocument(url: url), document.pageCount > 0 else {
            throw CestaOCRError.unreadableInput
        }
        return try (0..<document.pageCount).map { index in
            guard let page = document.page(at: index) else {
                throw CestaOCRError.unreadableInput
            }
            let bounds = page.bounds(for: .mediaBox)
            let scale: CGFloat = 2.5
            let size = NSSize(width: max(bounds.width * scale, 1200), height: max(bounds.height * scale, 1600))
            let image = page.thumbnail(of: size, for: .mediaBox)
            return try recognize(cgImage(from: image))
        }
    }
    guard let image = NSImage(contentsOf: url) else {
        throw CestaOCRError.unreadableInput
    }
    return [try recognize(cgImage(from: image))]
}

do {
    guard CommandLine.arguments.count == 2 else { throw CestaOCRError.invalidArguments }
    let input = URL(fileURLWithPath: CommandLine.arguments[1])
    let pages = try extractPages(from: input)
    let text = pages.map(\.text).filter { !$0.isEmpty }.joined(separator: "\n\n")
    guard !text.isEmpty else { throw CestaOCRError.noText }
    let confidences = pages.flatMap(\.confidences)
    let confidence: Any = confidences.isEmpty
        ? NSNull()
        : Double(confidences.reduce(0, +)) / Double(confidences.count)
    let payload: [String: Any] = [
        "text": text,
        "right_column_text": pages.map(\.rightColumnText).joined(separator: "\n\n"),
        "confidence": confidence,
        "page_count": pages.count,
    ]
    let data = try JSONSerialization.data(withJSONObject: payload, options: [])
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    let payload: [String: Any] = ["error": String(describing: error)]
    if let data = try? JSONSerialization.data(withJSONObject: payload, options: []) {
        FileHandle.standardError.write(data)
        FileHandle.standardError.write(Data("\n".utf8))
    }
    exit(2)
}
