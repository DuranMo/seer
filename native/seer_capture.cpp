// Optional window video and process-loopback audio. Standard desktop capture
// never starts this helper. Build with tools/build_native.ps1 (MinGW-w64).
#define _WIN32_WINNT 0x0A00
#include <windows.h>
#include <roapi.h>
#include <winstring.h>
#include <asyncinfo.h>
#include <windows.graphics.capture.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <windows.foundation.h>
#include <d3d11.h>
#include <audioclient.h>
#include <mmdeviceapi.h>
#include <propidl.h>
#include <wrl/client.h>
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <memory>

using Microsoft::WRL::ComPtr;
namespace Cap = ABI::Windows::Graphics::Capture;
namespace DX = ABI::Windows::Graphics::DirectX;
namespace D3 = ABI::Windows::Graphics::DirectX::Direct3D11;
std::atomic<bool> stopping{false};
void check(HRESULT hr) {
    if (FAILED(hr)) {
        char error[64]; std::snprintf(error, sizeof(error), "Windows capture error 0x%08lx", (unsigned long)hr);
        throw std::runtime_error(error);
    }
}
bool write_bytes(const void* data, size_t bytes) {
    auto p = static_cast<const unsigned char*>(data);
    while (bytes) {
        DWORD written = 0;
        if (!WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), p, (DWORD)std::min(bytes, size_t(65536)), &written, nullptr) || !written)
            return false;
        p += written; bytes -= written;
    }
    return true;
}
int64_t qpc100() {
    LARGE_INTEGER value, freq; QueryPerformanceCounter(&value); QueryPerformanceFrequency(&freq);
    return (int64_t)((long double)value.QuadPart * 10000000 / freq.QuadPart);
}
int64_t epoch100() {
    FILETIME ft; GetSystemTimePreciseAsFileTime(&ft);
    ULARGE_INTEGER value; value.LowPart = ft.dwLowDateTime; value.HighPart = ft.dwHighDateTime;
    return (int64_t)value.QuadPart - 116444736000000000LL;
}
struct String {
    HSTRING value = nullptr;
    String(const wchar_t* text) { check(WindowsCreateString(text, (UINT32)wcslen(text), &value)); }
    ~String() { WindowsDeleteString(value); }
};
// ABI declarations for the optional Windows borderless-consent API, absent
// from some MinGW headers. Windows still enforces the user's permission.
struct BorderAccessOperation : IInspectable {
    virtual HRESULT STDMETHODCALLTYPE put_Completed(IUnknown*) = 0;
    virtual HRESULT STDMETHODCALLTYPE get_Completed(IUnknown**) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetResults(INT32*) = 0;
};
struct BorderAccessFactory : IInspectable {
    virtual HRESULT STDMETHODCALLTYPE RequestAccessAsync(INT32, BorderAccessOperation**) = 0;
};
void request_borderless() {
    String name(L"Windows.Graphics.Capture.GraphicsCaptureAccess");
    const GUID iid{0x743ed370, 0x06ec, 0x5040, {0xa5, 0x8a, 0x90, 0x1f, 0x0f, 0x75, 0x70, 0x95}};
    ComPtr<BorderAccessFactory> factory;
    if (FAILED(RoGetActivationFactory(name.value, iid, (void**)factory.GetAddressOf()))) return;
    ComPtr<BorderAccessOperation> operation;
    if (FAILED(factory->RequestAccessAsync(0, &operation))) return; // Borderless
    ComPtr<IAsyncInfo> info;
    if (FAILED(operation.As(&info))) return;
    AsyncStatus status = Started;
    auto deadline = GetTickCount64() + 10000;
    while (SUCCEEDED(info->get_Status(&status)) && status == Started && !stopping && GetTickCount64() < deadline) Sleep(10);
    if (status == Completed) {
        INT32 permission = 0;
        if (SUCCEEDED(operation->GetResults(&permission)))
            fprintf(stderr, "Borderless capture access status: %d\n", (int)permission);
    } else if (status == Started) info->Cancel();
}
void close_inspectable(IUnknown* value) {
    ComPtr<ABI::Windows::Foundation::IClosable> closer;
    if (value && SUCCEEDED(value->QueryInterface(IID_PPV_ARGS(&closer)))) closer->Close();
}

struct WindowCapture {
    HWND window;
    ComPtr<Cap::IDirect3D11CaptureFramePool> pool;
    ComPtr<Cap::IGraphicsCaptureSession> session;
    ComPtr<ID3D11Texture2D> staging;
    ABI::Windows::Graphics::SizeInt32 size{};
    std::vector<uint32_t> pixels;
    unsigned width = 0, height = 0;
    bool got_frame = false;
    ~WindowCapture() { close_inspectable(session.Get()); close_inspectable(pool.Get()); }
};
void video(double fps, int width, int height, const std::vector<HWND>& windows) {
    if (windows.empty() || !std::isfinite(fps) || fps <= 0 || fps > 1000000 || width < 2 || height < 2 || width > 7680 || height > 4320)
        throw std::runtime_error("Invalid window capture selection.");
    ComPtr<ID3D11Device> device; ComPtr<ID3D11DeviceContext> context;
    check(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        nullptr, 0, D3D11_SDK_VERSION, &device, nullptr, &context));
    ComPtr<IDXGIDevice> dxgi; check(device.As(&dxgi));
    ComPtr<IInspectable> inspectable; check(CreateDirect3D11DeviceFromDXGIDevice(dxgi.Get(), &inspectable));
    ComPtr<D3::IDirect3DDevice> runtime_device; check(inspectable.As(&runtime_device));
    String item_name(L"Windows.Graphics.Capture.GraphicsCaptureItem"), pool_name(L"Windows.Graphics.Capture.Direct3D11CaptureFramePool");
    ComPtr<IGraphicsCaptureItemInterop> interop;
    check(RoGetActivationFactory(item_name.value, IID_PPV_ARGS(&interop)));
    ComPtr<Cap::IDirect3D11CaptureFramePoolStatics2> factory;
    check(RoGetActivationFactory(pool_name.value, IID_PPV_ARGS(&factory)));
    request_borderless();
    std::vector<std::unique_ptr<WindowCapture>> sources;
    for (HWND hwnd : windows) {
        if (!IsWindow(hwnd) || IsIconic(hwnd)) throw std::runtime_error("A selected window is closed or minimized. Restore it before starting capture.");
        auto source = std::make_unique<WindowCapture>(); source->window = hwnd;
        ComPtr<Cap::IGraphicsCaptureItem> item;
        check(interop->CreateForWindow(hwnd, IID_PPV_ARGS(&item)));
        check(item->get_Size(&source->size));
        check(factory->CreateFreeThreaded(runtime_device.Get(), DX::DirectXPixelFormat_B8G8R8A8UIntNormalized,
            2, source->size, &source->pool));
        check(source->pool->CreateCaptureSession(item.Get(), &source->session));
        ComPtr<Cap::IGraphicsCaptureSession3> border;
        if (SUCCEEDED(source->session.As(&border))) {
            // If permission is denied Windows ignores this request and keeps
            // its border. Older Windows versions keep their normal behavior.
            HRESULT result = border->put_IsBorderRequired(false);
            fprintf(stderr, "Borderless session request: 0x%08lx\n", (unsigned long)result);
        }
        check(source->session->StartCapture());
        sources.push_back(std::move(source));
    }
    const int columns = (int)std::ceil(std::sqrt((double)sources.size()));
    const int rows = ((int)sources.size() + columns - 1) / columns;
    std::vector<uint32_t> canvas((size_t)width * height, 0xff000000);
    auto start = GetTickCount64();
    auto next = qpc100();
    while (!stopping) {
        std::fill(canvas.begin(), canvas.end(), 0xff000000);
        bool ready = true;
        for (size_t i = 0; i < sources.size(); ++i) {
            auto& source = *sources[i];
            if (!IsWindow(source.window)) throw std::runtime_error("A selected window closed. Capture stopped.");
            if (IsIconic(source.window)) { source.got_frame = false; continue; }
            ComPtr<Cap::IDirect3D11CaptureFrame> frame;
            check(source.pool->TryGetNextFrame(&frame));
            if (frame) {
                ComPtr<D3::IDirect3DSurface> surface; check(frame->get_Surface(&surface));
                ComPtr<Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess> access; check(surface.As(&access));
                ComPtr<ID3D11Texture2D> texture; check(access->GetInterface(IID_PPV_ARGS(&texture)));
                D3D11_TEXTURE2D_DESC desc; texture->GetDesc(&desc);
                ABI::Windows::Graphics::SizeInt32 content; check(frame->get_ContentSize(&content));
                bool resized = content.Width != source.size.Width || content.Height != source.size.Height;
                unsigned w = std::min(desc.Width, (unsigned)std::max(1, content.Width));
                unsigned h = std::min(desc.Height, (unsigned)std::max(1, content.Height));
                D3D11_TEXTURE2D_DESC existing{}; if (source.staging) source.staging->GetDesc(&existing);
                if (!source.staging || existing.Width != desc.Width || existing.Height != desc.Height) {
                    source.staging.Reset();
                    desc.Usage = D3D11_USAGE_STAGING; desc.BindFlags = desc.MiscFlags = 0;
                    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
                    check(device->CreateTexture2D(&desc, nullptr, &source.staging));
                }
                context->CopyResource(source.staging.Get(), texture.Get());
                D3D11_MAPPED_SUBRESOURCE mapped; check(context->Map(source.staging.Get(), 0, D3D11_MAP_READ, 0, &mapped));
                source.pixels.resize((size_t)w * h);
                for (unsigned y = 0; y < h; ++y)
                    memcpy(source.pixels.data() + (size_t)y * w, (unsigned char*)mapped.pData + (size_t)y * mapped.RowPitch, (size_t)w * 4);
                context->Unmap(source.staging.Get(), 0);
                source.width = w; source.height = h; source.got_frame = true;
                close_inspectable(frame.Get()); frame.Reset();
                if (resized && content.Width > 0 && content.Height > 0) {
                    source.size = content;
                    check(source.pool->Recreate(runtime_device.Get(), DX::DirectXPixelFormat_B8G8R8A8UIntNormalized, 2, content));
                }
            }
            if (!source.got_frame) { ready = false; continue; }
            int cell_w = width / columns, cell_h = height / rows;
            double scale = std::min((double)cell_w / source.width, (double)cell_h / source.height);
            int draw_w = std::max(1, (int)(source.width * scale)), draw_h = std::max(1, (int)(source.height * scale));
            int left = (i % columns) * cell_w + (cell_w - draw_w) / 2;
            int top = (i / columns) * cell_h + (cell_h - draw_h) / 2;
            for (int y = 0; y < draw_h; ++y) {
                unsigned sy = std::min(source.height - 1, (unsigned)(y / scale));
                for (int x = 0; x < draw_w; ++x) {
                    unsigned sx = std::min(source.width - 1, (unsigned)(x / scale));
                    canvas[(size_t)(top + y) * width + left + x] = source.pixels[(size_t)sy * source.width + sx];
                }
            }
        }
        if (ready) { if (!write_bytes(canvas.data(), canvas.size() * 4)) break; }
        else if (GetTickCount64() - start > 10000) throw std::runtime_error("A selected window did not provide capture frames.");
        next += 10000000 / fps;
        int64_t remaining = next - qpc100();
        if (remaining > 10000) Sleep((DWORD)(remaining / 10000));
        if (remaining < -1000000) next = qpc100();
    }
}

struct Completion : IActivateAudioInterfaceCompletionHandler {
    LONG refs = 1;
    HANDLE event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    HRESULT result = E_PENDING;
    ComPtr<IAudioClient> client;
    ~Completion() { CloseHandle(event); }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** value) override {
        if (iid == __uuidof(IUnknown) || iid == __uuidof(IActivateAudioInterfaceCompletionHandler) || iid == __uuidof(IAgileObject)) {
            *value = static_cast<IActivateAudioInterfaceCompletionHandler*>(this); AddRef(); return S_OK;
        }
        *value = nullptr; return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&refs); }
    ULONG STDMETHODCALLTYPE Release() override { LONG count = InterlockedDecrement(&refs); if (!count) delete this; return count; }
    HRESULT STDMETHODCALLTYPE ActivateCompleted(IActivateAudioInterfaceAsyncOperation* operation) override {
        ComPtr<IUnknown> value;
        HRESULT hr = operation->GetActivateResult(&result, &value);
        if (FAILED(hr)) result = hr;
        if (SUCCEEDED(result)) result = value.As(&client);
        SetEvent(event); return S_OK;
    }
};
struct Audio {
    ComPtr<IAudioClient> client;
    ComPtr<IAudioCaptureClient> capture;
    HANDLE event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    std::vector<int16_t> ring = std::vector<int16_t>(48000 * 2 * 2);
    std::vector<int64_t> tags = std::vector<int64_t>(48000 * 2, -1);
    int64_t next_sample = 0;
    bool positioned = false;
    ~Audio() { if (client) client->Stop(); CloseHandle(event); }
    void start(DWORD pid) {
        ComPtr<Completion> completion; completion.Attach(new Completion());
        struct Params { int type; DWORD pid; int mode; } params{1, pid, 0};
        PROPVARIANT activation{}; activation.vt = VT_BLOB;
        activation.blob.cbSize = sizeof(params); activation.blob.pBlobData = (BYTE*)&params;
        ComPtr<IActivateAudioInterfaceAsyncOperation> operation;
        check(ActivateAudioInterfaceAsync(L"VAD\\Process_Loopback", __uuidof(IAudioClient), &activation, completion.Get(), &operation));
        if (WaitForSingleObject(completion->event, 10000) != WAIT_OBJECT_0) throw std::runtime_error("Application audio activation timed out.");
        check(completion->result); client = completion->client;
        WAVEFORMATEX format{WAVE_FORMAT_PCM, 2, 48000, 48000 * 4, 4, 16, 0};
        check(client->Initialize(AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_EVENTCALLBACK |
            AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY, 0, 0, &format, nullptr));
        check(client->SetEventHandle(event)); check(client->GetService(IID_PPV_ARGS(&capture))); check(client->Start());
    }
    void drain(int64_t origin_qpc, int64_t output_position) {
        UINT32 count = 0; check(capture->GetNextPacketSize(&count));
        while (count) {
            BYTE* data = nullptr; UINT32 frames = 0; DWORD flags = 0; UINT64 position = 0, qpc = 0;
            check(capture->GetBuffer(&data, &frames, &flags, &position, &qpc));
            int64_t start = (int64_t)std::llround(((long double)qpc - origin_qpc) * 48000 / 10000000);
            if (positioned && (flags & AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY)) {
                capture->ReleaseBuffer(frames); throw std::runtime_error("Application audio lost samples. Restart capture.");
            }
            if (positioned && std::llabs(start - next_sample) <= 960) start = next_sample;
            next_sample = start + frames; positioned = true;
            if (flags & AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR) { capture->ReleaseBuffer(frames); throw std::runtime_error("Application audio returned an invalid clock."); }
            if (start + frames < output_position - 4800) { capture->ReleaseBuffer(frames); throw std::runtime_error("Application audio arrived too late to remain synchronized."); }
            for (UINT32 i = 0; i < frames; ++i) {
                int64_t sample = start + i;
                if (sample < output_position || sample < 0) continue;
                if (sample - output_position >= 96000) { capture->ReleaseBuffer(frames); throw std::runtime_error("Application audio clock jumped."); }
                size_t slot = sample % 96000; tags[slot] = sample;
                ring[slot * 2] = flags & AUDCLNT_BUFFERFLAGS_SILENT ? 0 : ((int16_t*)data)[i * 2];
                ring[slot * 2 + 1] = flags & AUDCLNT_BUFFERFLAGS_SILENT ? 0 : ((int16_t*)data)[i * 2 + 1];
            }
            check(capture->ReleaseBuffer(frames)); check(capture->GetNextPacketSize(&count));
        }
    }
};
void audio(const std::vector<DWORD>& pids) {
    if (pids.empty()) throw std::runtime_error("Choose at least one audio application.");
    std::vector<std::unique_ptr<Audio>> sources;
    for (DWORD pid : pids) { auto source = std::make_unique<Audio>(); source->start(pid); sources.push_back(std::move(source)); }
    int64_t origin_qpc = qpc100(), origin_epoch = epoch100(), output_position = 0;
    if (!write_bytes("SEA1", 4)) return;
    auto emit_until = [&](int64_t available) {
        while (available - output_position >= 480) {
            int16_t mixed[960]{};
            for (int frame = 0; frame < 480; ++frame) {
                size_t slot = (output_position + frame) % 96000;
                int64_t left = 0, right = 0;
                for (auto& source : sources) if (source->tags[slot] == output_position + frame) {
                    left += source->ring[slot * 2]; right += source->ring[slot * 2 + 1];
                }
                mixed[frame * 2] = (int16_t)(left / (int)sources.size());
                mixed[frame * 2 + 1] = (int16_t)(right / (int)sources.size());
            }
            uint32_t frames = 480; int64_t stamp = origin_epoch + output_position * 10000000 / 48000;
            if (!write_bytes(&frames, 4) || !write_bytes(&stamp, 8) || !write_bytes(mixed, sizeof(mixed))) return false;
            output_position += frames;
        }
        return true;
    };
    while (!stopping) {
        for (auto& source : sources) source->drain(origin_qpc, output_position);
        // A small fixed latency lets all sources arrive before mixing. Packets
        // carry their original capture time, so this does not shift A/V sync.
        int64_t available = (qpc100() - origin_qpc - 1000000) * 48000 / 10000000;
        if (!emit_until(available)) return;
        Sleep(5);
    }
    int64_t end = (qpc100() - origin_qpc) * 48000 / 10000000;
    Sleep(20);
    for (auto& source : sources) source->drain(origin_qpc, output_position);
    emit_until(end);
}
int wmain(int argc, wchar_t** argv) {
    try {
        check(RoInitialize(RO_INIT_MULTITHREADED));
        std::thread([] { char value; DWORD read; while (ReadFile(GetStdHandle(STD_INPUT_HANDLE), &value, 1, &read, nullptr) && read) if (value == 'q') break; stopping = true; }).detach();
        if (argc >= 6 && std::wstring(argv[1]) == L"video") {
            std::vector<HWND> windows; for (int i = 5; i < argc; ++i) windows.push_back((HWND)(uintptr_t)std::stoull(argv[i]));
            video(std::stod(argv[2]), std::stoi(argv[3]), std::stoi(argv[4]), windows);
        } else if (argc >= 3 && std::wstring(argv[1]) == L"audio") {
            std::vector<DWORD> pids; for (int i = 2; i < argc; ++i) pids.push_back((DWORD)std::stoul(argv[i]));
            audio(pids);
        } else throw std::runtime_error("Invalid capture helper arguments.");
        return 0;
    } catch (const std::exception& error) { fprintf(stderr, "%s\n", error.what()); return 1; }
}
