import SwiftUI

struct ConnectionView: View {
    @EnvironmentObject private var model: AppModel
    @FocusState private var focusedField: Field?

    private enum Field {
        case server
        case key
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                brand
                VStack(alignment: .leading, spacing: 9) {
                    Text("Connect your server")
                        .font(.system(size: 25, weight: .bold, design: .rounded))
                        .foregroundStyle(.white)
                    Text("Ember runs privately on your own machine. Point this iPhone at the HTTPS address from Tailscale Serve.")
                        .font(.system(size: 15))
                        .foregroundStyle(EmberTheme.secondaryText)
                        .fixedSize(horizontal: false, vertical: true)
                }
                form
                privacyNote
            }
            .padding(.horizontal, 22)
            .padding(.top, 30)
            .padding(.bottom, 34)
        }
        .scrollDismissesKeyboard(.interactively)
    }

    private var brand: some View {
        HStack(spacing: 11) {
            Image(systemName: "flame.fill")
                .font(.system(size: 31, weight: .bold))
                .foregroundStyle(EmberTheme.accentGradient)
                .shadow(color: EmberTheme.pink.opacity(0.4), radius: 18)
            Text("Ember")
                .font(.system(size: 35, weight: .heavy, design: .rounded))
                .foregroundStyle(.white)
        }
    }

    private var form: some View {
        VStack(alignment: .leading, spacing: 17) {
            VStack(alignment: .leading, spacing: 7) {
                fieldLabel("SERVER ADDRESS")
                TextField("https://ember.your-tailnet.ts.net", text: $model.serverURL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                    .focused($focusedField, equals: .server)
                    .submitLabel(.next)
                    .onSubmit { focusedField = .key }
                    .foregroundStyle(.white)
                    .tint(EmberTheme.pink)
                    .padding(.horizontal, 14)
                    .frame(height: 48)
                    .background(EmberTheme.surfaceRaised)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .overlay(inputBorder)
            }

            VStack(alignment: .leading, spacing: 7) {
                fieldLabel("ACCESS KEY")
                SecureField("Paste EMBER_API_TOKEN", text: $model.accessKey)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($focusedField, equals: .key)
                    .submitLabel(.go)
                    .onSubmit(connect)
                    .foregroundStyle(.white)
                    .tint(EmberTheme.pink)
                    .padding(.horizontal, 14)
                    .frame(height: 48)
                    .background(EmberTheme.surfaceRaised)
                    .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .overlay(inputBorder)
            }

            if let error = model.connectionError {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 12))
                    Text(error)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .font(.system(size: 13))
                .foregroundStyle(EmberTheme.pinkSoft)
            }

            Button(action: connect) {
                if model.isConnecting {
                    ProgressView()
                        .tint(.black)
                } else {
                    Text("Connect")
                }
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(model.isConnecting)
        }
        .padding(18)
        .emberCard()
    }

    private var privacyNote: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "lock.shield.fill")
                .font(.system(size: 14))
                .foregroundStyle(EmberTheme.pink)
            Text("The key stays in the iPhone Keychain and is sent only in an Authorization header. Use HTTPS over Tailscale; never expose the backend port publicly.")
                .font(.system(size: 12))
                .foregroundStyle(EmberTheme.faintText)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 4)
    }

    private var inputBorder: some View {
        RoundedRectangle(cornerRadius: 12, style: .continuous)
            .stroke(EmberTheme.hairline, lineWidth: 1)
    }

    private func fieldLabel(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 10, weight: .bold))
            .tracking(1.4)
            .foregroundStyle(EmberTheme.faintText)
    }

    private func connect() {
        focusedField = nil
        Task {
            await model.connect()
        }
    }
}
