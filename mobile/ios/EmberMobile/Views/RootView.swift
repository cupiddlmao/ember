import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        ZStack {
            EmberTheme.backgroundGradient.ignoresSafeArea()

            if model.isRestoring {
                SplashView()
            } else if model.isConnected {
                SearchView(player: model.player)
            } else {
                ConnectionView()
            }
        }
        .preferredColorScheme(.dark)
        .task {
            await model.restoreSession()
        }
        .sheet(item: $model.activeSheet) { sheet in
            switch sheet {
            case .nowPlaying:
                NowPlayingView(player: model.player)
                    .environmentObject(model)
            case .settings:
                SettingsView()
                    .environmentObject(model)
            }
        }
        .alert(
            "Ember",
            isPresented: Binding(
                get: { model.errorMessage != nil },
                set: { isPresented in
                    if !isPresented {
                        model.clearError()
                    }
                }
            )
        ) {
            Button("OK", role: .cancel) {
                model.clearError()
            }
        } message: {
            Text(model.errorMessage ?? "")
        }
    }
}

private struct SplashView: View {
    var body: some View {
        VStack(spacing: 18) {
            Image(systemName: "flame.fill")
                .font(.system(size: 52, weight: .bold))
                .foregroundStyle(EmberTheme.accentGradient)
                .shadow(color: EmberTheme.pink.opacity(0.45), radius: 24)
            Text("Ember")
                .font(.system(size: 38, weight: .heavy, design: .rounded))
                .foregroundStyle(.white)
            ProgressView()
                .tint(EmberTheme.pink)
                .padding(.top, 6)
        }
    }
}
