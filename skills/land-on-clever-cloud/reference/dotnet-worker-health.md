# .NET background worker: the minimal health endpoint

The deployer checks that something listens on `$PORT` (8080) before a deployment succeeds, so a
queue worker with no HTTP server fails as is. Add this class to the worker and call
`Health.Start();` first in `Main`, before the loop. No package, no ASP.NET: `HttpListener` from the
base library. Checked on 2026-10-25 with the .NET 8 SDK (8.0.131): `dotnet build` passes, and with
`PORT=8099` a `GET /` answers `200 ok` on all interfaces (`selftest.sh` compiles this exact block, verbatim,
in a project with implicit usings OFF, when `dotnet` is installed).

```csharp
using System;
using System.Net;
using System.Text;
using System.Threading;

// Minimal health endpoint for a background worker on Clever Cloud: the deployer checks that
// something listens on $PORT (8080) before the deployment succeeds. Start it, then run the loop.
static class Health
{
    public static void Start()
    {
        var port = Environment.GetEnvironmentVariable("PORT") ?? "8080";
        var listener = new HttpListener();
        listener.Prefixes.Add($"http://*:{port}/");   // all interfaces (0.0.0.0), required on Clever
        listener.Start();
        new Thread(() =>
        {
            var ok = Encoding.UTF8.GetBytes("ok");
            while (listener.IsListening)
            {
                try
                {
                    var ctx = listener.GetContext();
                    ctx.Response.StatusCode = 200;
                    ctx.Response.ContentType = "text/plain";
                    ctx.Response.OutputStream.Write(ok, 0, ok.Length);
                    ctx.Response.Close();
                }
                catch (HttpListenerException) { break; }
            }
        }) { IsBackground = true, Name = "health" }.Start();
        Console.WriteLine($"health endpoint listening on 0.0.0.0:{port}");
    }
}
```

In the voting app's `worker/Program.cs`: add the class (or a `Health.cs` file next to it) and call
`Health.Start();` as the first line of `Main`. The project must target an accepted framework
(`net8.0`, see `runtimes-hooks.md`), and the Dockerfile's `sdk`/`runtime` images must match it so
docker-compose keeps working.
