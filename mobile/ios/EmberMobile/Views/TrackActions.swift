import SwiftUI

struct SongRadioButton: View {
    @EnvironmentObject private var model: AppModel
    let track: Track

    var body: some View {
        Button {
            model.startSongRadio(track)
        } label: {
            Label(
                model.radioTrackID == track.id ? "Loading radio…" : "Song radio",
                systemImage: "dot.radiowaves.left.and.right"
            )
        }
        .disabled(model.radioTrackID == track.id)
    }
}
