package bodytap

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"hash"
	"io"
	"net/http"
	"os"
	"path/filepath"

	"github.com/caddyserver/caddy/v2"
	"github.com/caddyserver/caddy/v2/caddyconfig/caddyfile"
	"github.com/caddyserver/caddy/v2/caddyconfig/httpcaddyfile"
	"github.com/caddyserver/caddy/v2/modules/caddyhttp"
	"go.uber.org/zap"
)

type BodyTap struct {
	Directory string `json:"directory,omitempty"`
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
	directory string
	requestID string
	file      *os.File
	hash      hash.Hash
	total     int64
	stored    int64
	sawEOF    bool
	writeErr  error
}

func (b *capturedBody) Read(p []byte) (int, error) {
	n, err := b.ReadCloser.Read(p)
	b.total += int64(n)
	if err == io.EOF {
		b.sawEOF = true
	}
	if n == 0 || b.writeErr != nil {
		return n, err
	}
	if b.file == nil {
		b.file, b.writeErr = os.OpenFile(
			filepath.Join(b.directory, b.requestID+".part"),
			os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600,
		)
		if b.writeErr != nil {
			return n, err
		}
		b.hash = sha256.New()
	}
	written, writeErr := b.file.Write(p[:n])
	if written > 0 {
		_, _ = b.hash.Write(p[:written])
		b.stored += int64(written)
	}
	if writeErr != nil {
		b.writeErr = writeErr
	} else if written != n {
		b.writeErr = io.ErrShortWrite
	}
	return n, err
}

func (b *capturedBody) finish() (string, string) {
	if b.file == nil {
		return "", ""
	}
	if err := b.file.Close(); err != nil && b.writeErr == nil {
		b.writeErr = err
	}
	part := filepath.Join(b.directory, b.requestID+".part")
	if b.writeErr != nil {
		_ = os.Remove(part)
		return "", ""
	}
	if err := os.Rename(part, filepath.Join(b.directory, b.requestID+".body")); err != nil {
		b.writeErr = err
		_ = os.Remove(part)
		return "", ""
	}
	return b.requestID, hex.EncodeToString(b.hash.Sum(nil))
}

func (m BodyTap) ServeHTTP(w http.ResponseWriter, r *http.Request, next caddyhttp.Handler) error {
	requestIDBytes := make([]byte, 16)
	if _, err := rand.Read(requestIDBytes); err != nil {
		return err
	}
	requestID := hex.EncodeToString(requestIDBytes)
	r.Header.Set("X-Blue-Request-ID", requestID)
	body := &capturedBody{
		ReadCloser: r.Body,
		directory:  m.Directory,
		requestID:  requestID,
	}
	if r.Body != nil {
		r.Body = body
	}
	err := next.ServeHTTP(w, r)
	ref, digest := body.finish()
	fields, ok := r.Context().Value(caddyhttp.ExtraLogFieldsCtxKey).(*caddyhttp.ExtraLogFields)
	if !ok {
		return err
	}
	fields.Set(zap.String("blue_request_id", requestID))
	status := "none"
	if body.writeErr != nil {
		status = "storage_error"
	} else if body.total > 0 {
		if r.ContentLength >= 0 && body.total < r.ContentLength || r.ContentLength < 0 && !body.sawEOF {
			status = "incomplete"
		} else {
			status = "captured"
		}
	} else if r.ContentLength > 0 {
		status = "unread"
	}
	fields.Set(zap.String("blue_body_status", status))
	fields.Set(zap.Int64("blue_body_bytes_read", body.total))
	fields.Set(zap.Int64("blue_body_bytes_stored", body.stored))
	if ref != "" {
		fields.Set(zap.String("blue_body_ref", ref))
		fields.Set(zap.String("blue_body_sha256", digest))
	}
	return err
}

func (m *BodyTap) UnmarshalCaddyfile(d *caddyfile.Dispenser) error {
	d.Next()
	m.Directory = "/body-spool"
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
