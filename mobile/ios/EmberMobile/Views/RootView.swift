import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @StateObject private var library = SharedLibraryModel()

    var body: some View {
        ZStack {
            EmberTheme.backgroundGradient.ignoresSafeArea()

            if model.isRestoring {
                SplashView()
            } else if model.isConnected {
                SignedInView()
            } else {
                ConnectionView()
            }
        }
        .environmentObject(library)
        .task(id: model.client?.identity) {
            library.configure(model.client)
            await library.refresh()
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

private struct SignedInView: View {
    @EnvironmentObject private var model: AppModel
    @State private var selectedTab = 0

    var body: some View {
        tabContent
            .animation(.easeInOut(duration: 0.22), value: model.player.track?.id)
    }

    @ViewBuilder
    private var tabContent: some View {
        if #available(iOS 26.1, *) {
            tabs
                .tabViewBottomAccessory(isEnabled: model.queue.current != nil) {
                    MiniPlayerBar(player: model.player)
                }
        } else if #available(iOS 26.0, *) {
            tabs
                .tabViewBottomAccessory {
                    MiniPlayerBar(player: model.player)
                }
        } else {
            tabs
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    if model.queue.current != nil {
                        MiniPlayerBar(player: model.player)
                            .transition(.move(edge: .bottom).combined(with: .opacity))
                    }
                }
        }
    }

    private var tabs: some View {
        TabView(selection: $selectedTab) {
            SearchView()
                .tabItem {
                    Label("Search", systemImage: "magnifyingglass")
                }
                .tag(0)

            LibraryView()
                .tabItem {
                    Label("Library", systemImage: "music.note.list")
                }
                .tag(1)
        }
        .tint(EmberTheme.pink)
        .toolbarBackground(EmberTheme.background, for: .tabBar)
        .toolbarBackground(.visible, for: .tabBar)
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
