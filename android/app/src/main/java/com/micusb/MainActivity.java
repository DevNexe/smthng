package com.micusb;

import android.app.Activity;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTrack;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.WindowManager;
import android.widget.*;

import java.net.*;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Collections;
import java.util.Locale;

public class MainActivity extends Activity {
    static final int PORT_DEFAULT = 42042, SAMPLE_RATE = 48000, FRAME_SAMPLES = 480, HEADER = 12, MAGIC = 0x4D555331;
    EditText port; TextView status, meter, addr; Button toggle;
    volatile boolean running; volatile DatagramSocket socket; Thread worker;
    int purple = Color.rgb(167,139,250), panel = Color.rgb(32,32,32);

    @Override public void onCreate(Bundle b) { super.onCreate(b); buildUi(); }

    TextView label(String text) { TextView v=new TextView(this); v.setText(text); v.setTextColor(Color.rgb(150,150,150)); v.setTextSize(12); v.setPadding(4,12,4,5); return v; }
    GradientDrawable bg(int color, int radius) { GradientDrawable g=new GradientDrawable(); g.setColor(color); g.setCornerRadius(radius); return g; }

    void buildUi() {
        LinearLayout root=new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setPadding(22,18,22,20); root.setBackgroundColor(Color.rgb(17,17,17));
        TextView title=new TextView(this); title.setText("MIC USB BRIDGE"); title.setTextColor(Color.WHITE); title.setTextSize(22); title.setTypeface(null,1); root.addView(title,new LinearLayout.LayoutParams(-1,58));
        TextView sub=new TextView(this); sub.setText("Приём с ПК • PCM 48 kHz • USB-модем"); sub.setTextColor(Color.rgb(130,130,130)); sub.setTextSize(13); root.addView(sub,new LinearLayout.LayoutParams(-1,40));
        LinearLayout card=new LinearLayout(this); card.setOrientation(LinearLayout.VERTICAL); card.setPadding(18,12,18,18); card.setBackground(bg(panel,18)); root.addView(card,new LinearLayout.LayoutParams(-1,-2));
        card.addView(label("АДРЕС ТЕЛЕФОНА (укажите его на ПК)")); addr=new TextView(this); addr.setTextColor(Color.WHITE); addr.setTextSize(15); addr.setPadding(14,14,14,14); addr.setBackground(bg(Color.rgb(42,42,42),12)); card.addView(addr,new LinearLayout.LayoutParams(-1,-2));
        card.addView(label("UDP ПОРТ")); port=new EditText(this); port.setSingleLine(); port.setText(String.valueOf(PORT_DEFAULT)); port.setTextColor(Color.WHITE); port.setInputType(2); port.setPadding(14,0,14,0); port.setBackground(bg(Color.rgb(42,42,42),12)); card.addView(port,new LinearLayout.LayoutParams(-1,52));
        toggle=new Button(this); toggle.setText("НАЧАТЬ ПРИЁМ"); toggle.setTextColor(Color.WHITE); toggle.setAllCaps(false); toggle.setTextSize(15); toggle.setBackground(bg(purple,14)); LinearLayout.LayoutParams bp=new LinearLayout.LayoutParams(-1,56); bp.topMargin=20; card.addView(toggle,bp); toggle.setOnClickListener(v->{if(running) stopStreaming(); else startStreaming();});
        status=new TextView(this); status.setText("Готов к подключению"); status.setTextColor(Color.rgb(150,150,150)); status.setTextSize(14); status.setPadding(4,24,4,4); root.addView(status,new LinearLayout.LayoutParams(-1,-2));
        meter=new TextView(this); meter.setText("○  Уровень сигнала  —"); meter.setTextColor(Color.rgb(100,100,100)); meter.setTextSize(14); meter.setPadding(4,16,4,4); root.addView(meter,new LinearLayout.LayoutParams(-1,-2));
        TextView hint=new TextView(this); hint.setText("Включите USB-модем, подключите ПК, нажмите «Начать приём», затем на ПК укажите адрес телефона и порт и запустите передачу."); hint.setTextColor(Color.rgb(100,100,100)); hint.setTextSize(12); hint.setPadding(4,20,4,0); root.addView(hint,new LinearLayout.LayoutParams(-1,-2)); setContentView(root);
        refreshAddresses();
    }

    void refreshAddresses() {
        StringBuilder sb=new StringBuilder();
        try { for(NetworkInterface ni:Collections.list(NetworkInterface.getNetworkInterfaces())) { if(!ni.isUp()||ni.isLoopback()) continue; for(InetAddress a:Collections.list(ni.getInetAddresses())) if(a instanceof Inet4Address) { if(sb.length()>0) sb.append("\n"); sb.append(ni.getName()).append(":  ").append(a.getHostAddress()); } } } catch(Exception ignored) {}
        addr.setText(sb.length()>0?sb.toString():"IP не найден — включите USB-модем");
    }

    void startStreaming() {
        if(worker!=null&&worker.isAlive()) return;
        final int p;
        try { p=Integer.parseInt(port.getText().toString().trim()); if(p<1024||p>65535) throw new NumberFormatException(); } catch(NumberFormatException e) { status.setText("Неверный порт (1024–65535)"); return; }
        refreshAddresses(); running=true; toggle.setText("ОСТАНОВИТЬ ПРИЁМ"); status.setText("● Приём на UDP :"+p+"\nОжидание ПК");
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        worker=new Thread(()->receive(p),"mic-usb-audio"); worker.start();
    }

    void receive(int p) {
        DatagramSocket s=null; AudioTrack track=null; String err=null;
        try {
            s=new DatagramSocket(null); s.setReuseAddress(true); s.setReceiveBufferSize(16*1024); s.setSoTimeout(300); s.bind(new InetSocketAddress(p)); socket=s;
            int min=AudioTrack.getMinBufferSize(SAMPLE_RATE,AudioFormat.CHANNEL_OUT_MONO,AudioFormat.ENCODING_PCM_16BIT);
            track=new AudioTrack.Builder()
                .setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                .setAudioFormat(new AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT).setSampleRate(SAMPLE_RATE).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                .setBufferSizeInBytes(Math.max(min,FRAME_SAMPLES*2*4))
                .setTransferMode(AudioTrack.MODE_STREAM)
                .setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
                .build();
            track.play();
            byte[] buf=new byte[HEADER+FRAME_SAMPLES*2+64]; DatagramPacket dp=new DatagramPacket(buf,buf.length);
            long expected=-1, packets=0, lost=0, lastUi=0;
            while(running) {
                dp.setLength(buf.length);
                try { s.receive(dp); } catch(SocketTimeoutException e) { continue; }
                if(dp.getLength()<HEADER) continue;
                ByteBuffer bb=ByteBuffer.wrap(buf,0,dp.getLength()).order(ByteOrder.BIG_ENDIAN);
                if(bb.getInt()!=MAGIC) continue;
                long seq=bb.getInt()&0xFFFFFFFFL; int n=bb.getInt();
                if(n<=0||n>FRAME_SAMPLES||dp.getLength()<HEADER+n*2) continue;
                if(expected>=0&&seq>expected) lost+=seq-expected;
                expected=seq+1; packets++;
                track.write(buf,HEADER,n*2);
                long now=SystemClock.elapsedRealtime();
                if(now-lastUi>=150) { lastUi=now; final int lv=level(buf,n); final long pk=packets, ls=lost;
                    runOnUiThread(()->{ if(!running) return; meter.setText(String.format(Locale.US,"●  Уровень сигнала  %d%%",lv)); status.setText(String.format(Locale.US,"● Приём сигнала\nпакетов: %d • потеряно: %d",pk,ls)); }); }
            }
        } catch(Exception e) { if(running) err="Ошибка: "+e.getMessage(); }
        finally {
            if(track!=null){try{track.stop();}catch(Exception ignored){} track.release();}
            if(s!=null) s.close(); socket=null;
            final String msg=err; runOnUiThread(()->onStopped(msg));
        }
    }

    // payload — signed little-endian PCM16
    int level(byte[] b,int n){ long sum=0; for(int i=0;i<n;i++){ short v=(short)((b[HEADER+2*i]&0xFF)|(b[HEADER+2*i+1]<<8)); sum+=(long)v*v; } return Math.min(100,(int)(Math.sqrt(sum/Math.max(1,n))/327.68)); }

    void stopStreaming() { running=false; toggle.setEnabled(false); DatagramSocket s=socket; if(s!=null) s.close(); }

    void onStopped(String err) {
        running=false; toggle.setEnabled(true); toggle.setText("НАЧАТЬ ПРИЁМ"); meter.setText("○  Уровень сигнала  —");
        status.setText(err!=null?err:"Готов к подключению");
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
    }

    @Override protected void onDestroy() { running=false; DatagramSocket s=socket; if(s!=null) s.close(); super.onDestroy(); }
}
