param(
    [switch]$Legacy,
    [int]$Port = 8887,
    [string]$Host = "127.0.0.1"
)

$arguments = @("-m", "backend.app.main", "--host", $Host, "--port", $Port, "--reload")
if ($Legacy) {
    $arguments += "--legacy"
}

python @arguments
