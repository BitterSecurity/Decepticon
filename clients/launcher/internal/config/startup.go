package config

import (
	"os"
	"strconv"
)

// StartupTimeoutSeconds is the shared Compose startup budget. The launcher and
// its opscontrol daemon must normalize interpolation identically to avoid
// configuration drift and premature health failures.
func StartupTimeoutSeconds() string {
	if value := os.Getenv("DECEPTICON_STARTUP_TIMEOUT_SECONDS"); value != "" {
		if seconds, err := strconv.Atoi(value); err == nil && seconds > 0 {
			return strconv.Itoa(seconds)
		}
	}
	return "600"
}
