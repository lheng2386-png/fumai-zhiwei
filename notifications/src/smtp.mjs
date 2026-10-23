import net from 'node:net';
import tls from 'node:tls';
import readline from 'node:readline';
import { once } from 'node:events';
import { randomUUID } from 'node:crypto';

function timeout(promise, milliseconds, message = 'SMTP 请求超时') {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => { timer = setTimeout(() => reject(Object.assign(new Error(message), { code: 'SMTP_TIMEOUT' })), milliseconds); })
  ]).finally(() => clearTimeout(timer));
}

function cleanHeader(value) {
  return String(value ?? '').replace(/[\r\n]+/g, ' ').trim();
}

function mailbox(value) {
  const matched = String(value ?? '').match(/<([^<>]+)>/);
  return cleanHeader(matched ? matched[1] : value);
}

function encodedHeader(value) {
  return `=?UTF-8?B?${Buffer.from(cleanHeader(value)).toString('base64')}?=`;
}

function dotStuff(value) {
  return String(value).replace(/\r?\n/g, '\r\n').replace(/^\./gm, '..');
}

function createMessage({ from, to, subject, text, html, messageId }) {
  const boundary = `fumai-${randomUUID()}`;
  const text64 = Buffer.from(text || '').toString('base64').replace(/(.{76})/g, '$1\r\n');
  const html64 = Buffer.from(html || '').toString('base64').replace(/(.{76})/g, '$1\r\n');
  return dotStuff([
    `From: ${cleanHeader(from)}`,
    `To: ${cleanHeader(to)}`,
    `Subject: ${encodedHeader(subject)}`,
    `Message-ID: <${messageId}>`,
    `Date: ${new Date().toUTCString()}`,
    'MIME-Version: 1.0',
    `Content-Type: multipart/alternative; boundary="${boundary}"`,
    '',
    `--${boundary}`,
    'Content-Type: text/plain; charset=UTF-8',
    'Content-Transfer-Encoding: base64',
    '', text64,
    `--${boundary}`,
    'Content-Type: text/html; charset=UTF-8',
    'Content-Transfer-Encoding: base64',
    '', html64,
    `--${boundary}--`, ''
  ].join('\r\n'));
}

class SmtpConnection {
  constructor(socket, timeoutMs) {
    this.socket = socket;
    this.timeoutMs = timeoutMs;
    this.lines = readline.createInterface({ input: socket, crlfDelay: Infinity })[Symbol.asyncIterator]();
  }

  async reply() {
    const first = await timeout(this.lines.next(), this.timeoutMs);
    if (first.done) throw Object.assign(new Error('SMTP 连接意外关闭'), { code: 'SMTP_CONNECTION_CLOSED' });
    const match = String(first.value).match(/^(\d{3})([ -])(.*)$/);
    if (!match) throw Object.assign(new Error('SMTP 响应格式无效'), { code: 'SMTP_BAD_RESPONSE' });
    const code = Number(match[1]);
    const lines = [match[3]];
    if (match[2] === '-') {
      while (true) {
        const next = await timeout(this.lines.next(), this.timeoutMs);
        if (next.done) throw Object.assign(new Error('SMTP 多行响应未完成'), { code: 'SMTP_CONNECTION_CLOSED' });
        const continuation = String(next.value).match(/^(\d{3})([ -])(.*)$/);
        lines.push(continuation ? continuation[3] : String(next.value));
        if (continuation && Number(continuation[1]) === code && continuation[2] === ' ') break;
      }
    }
    return { code, text: lines.join(' ') };
  }

  async command(command, accepted) {
    if (command != null) this.socket.write(`${command}\r\n`);
    const reply = await this.reply();
    if (!accepted.includes(reply.code)) {
      const error = new Error(`SMTP ${reply.code}: ${reply.text}`);
      error.code = `SMTP_${reply.code}`;
      error.transient = reply.code >= 400 && reply.code < 500;
      throw error;
    }
    return reply;
  }
}

export async function sendSmtpMail(config, message) {
  if(config.secure===false && !['127.0.0.1','localhost','::1'].includes(config.host))throw Object.assign(new Error('公网 SMTP 必须使用 TLS 加密；当前支持 465 隐式 TLS'),{code:'SMTP_TLS_REQUIRED'});
  const timeoutMs = Number(config.timeoutMs || 10_000);
  const socket = config.secure === false
    ? net.createConnection({ host: config.host, port: config.port })
    : tls.connect({ host: config.host, port: config.port, servername: config.host, minVersion: 'TLSv1.2' });
  let bodySubmitted = false;
  try {
    await timeout(once(socket, config.secure === false ? 'connect' : 'secureConnect'), timeoutMs, 'SMTP 连接超时');
    socket.setTimeout(timeoutMs, () => socket.destroy(Object.assign(new Error('SMTP 套接字超时'), { code: 'SMTP_TIMEOUT' })));
    const smtp = new SmtpConnection(socket, timeoutMs);
    await smtp.command(null, [220]);
    await smtp.command(`EHLO ${cleanHeader(config.helo || 'fumai-notifier.local')}`, [250]);
    if (config.user) {
      await smtp.command('AUTH LOGIN', [334]);
      await smtp.command(Buffer.from(config.user).toString('base64'), [334]);
      await smtp.command(Buffer.from(config.password || '').toString('base64'), [235]);
    }
    await smtp.command(`MAIL FROM:<${mailbox(config.from)}>`, [250]);
    await smtp.command(`RCPT TO:<${mailbox(message.to)}>`, [250, 251]);
    await smtp.command('DATA', [354]);
    bodySubmitted = true;
    const messageId = `${randomUUID()}@${cleanHeader(config.helo || 'fumai-notifier.local')}`;
    socket.write(`${createMessage({ ...message, from: config.from, messageId })}.\r\n`);
    await smtp.command(null, [250]);
    bodySubmitted = false;
    // DATA 250 is the acceptance boundary. A QUIT disconnect must not turn an accepted mail into a retry.
    try { await smtp.command('QUIT', [221]); } catch { /* Already accepted by the SMTP server. */ }
    return { accepted: true, simulated: false, providerMessageId: messageId };
  } catch (error) {
    error.unknownAcceptance = bodySubmitted;
    throw error;
  } finally {
    socket.destroy();
  }
}
