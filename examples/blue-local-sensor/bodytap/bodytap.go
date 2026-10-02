package bodytap

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"io"
	"net/http"
	"strconv"

	"github.com/caddyserver/caddy/v2"
	"github.com/caddyserver/caddy/v2/caddyconfig/caddyfile"
	"github.com/caddyserver/caddy/v2/caddyconfig/httpcaddyfile"
	"github.com/caddyserver/caddy/v2/modules/caddyhttp"
	"go.uber.org/zap"
)

const defaultMaxBytes = 64 * 1024

type BodyTap struct {
	MaxBytes int `json:"max_bytes,omitempty"`
}

func init() {
	caddy.RegisterModule(BodyTap{})
	httpcaddyfile.RegisterHandlerDirective("blue_body", parseCaddyfile)
}

func (BodyTap) CaddyModule() caddy.ModuleInfo {
	return caddy.ModuleInfo{
		ID:  "http.handlers.blue_body",
		New: func() caddy.Module { return new(BodyTap) },
	}
}

type capturedBody struct {
	io.ReadCloser
	data  []byte
	max   int
	total int64
}

func (b *capturedBody) Read(p []byte) (int, error) {
	n, err := b.ReadCloser.Read(p)
	b.total += int64(n)
	if len(b.data) <= b.max {
		remaining := b.max + 1 - len(b.data)
		if n < remaining {
			remaining = n
		}
		b.data = append(b.data, p[:remaining]...)
	}
	return n, err
}

func (m BodyTap) ServeHTTP(w http.ResponseWriter, r *http.Request, next caddyhttp.Handler) error {
	requestIDBytes := make([]byte, 16)
	if _, err := rand.Read(requestIDBytes); err != nil {
		return err
	}
	requestID := hex.EncodeToString(requestIDBytes)
	r.Header.Set("X-Blue-Request-ID", requestID)
	maxBytes := m.MaxBytes
	if maxBytes == 0 {
		maxBytes = defaultMaxBytes
	}
	body := &capturedBody{ReadCloser: r.Body, max: maxBytes}
	if r.Body != nil {
		r.Body = body
	}
	err := next.ServeHTTP(w, r)
	fields, ok := r.Context().Value(caddyhttp.ExtraLogFieldsCtxKey).(*caddyhttp.ExtraLogFields)
	if !ok {
		return err
	}
	fields.Set(zap.String("blue_request_id", requestID))
	status := "none"
	if body.total > int64(maxBytes) || r.ContentLength > int64(maxBytes) {
		status = "truncated"
	} else if body.total > 0 {
		if r.ContentLength >= 0 && body.total < r.ContentLength {
			status = "incomplete"
		} else {
			status = "captured"
		}
	} else if r.ContentLength > 0 {
		status = "unread"
	}
	fields.Set(zap.String("blue_body_status", status))
	fields.Set(zap.Int64("blue_body_bytes_read", body.total))
	if len(body.data) > 0 {
		captured := body.data
		if len(captured) > maxBytes {
			captured = captured[:maxBytes]
		}
		fields.Set(zap.String("blue_request_body_base64", base64.StdEncoding.EncodeToString(captured)))
	}
	return err
}

func (m *BodyTap) UnmarshalCaddyfile(d *caddyfile.Dispenser) error {
	d.Next()
	if d.NextArg() {
		value, err := strconv.Atoi(d.Val())
		if err != nil || value < 1 || value > 8*1024*1024 {
			return d.Err("blue_body requires a byte limit between 1 and 8388608")
		}
		m.MaxBytes = value
	}
	if d.NextArg() {
		return d.ArgErr()
	}
	return nil
}

func parseCaddyfile(h httpcaddyfile.Helper) (caddyhttp.MiddlewareHandler, error) {
	var module BodyTap
	err := module.UnmarshalCaddyfile(h.Dispenser)
	return &module, err
}

var _ caddyhttp.MiddlewareHandler = (*BodyTap)(nil)
var _ caddyfile.Unmarshaler = (*BodyTap)(nil)
