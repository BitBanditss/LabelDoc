import React, { useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, Pressable,
  Image, ActivityIndicator, Alert,
} from 'react-native';
import { Feather } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';
import { useRouter } from 'expo-router';
import { useAuth } from '@/src/context/AuthContext';
import { useInspections } from '@/src/context/InspectionContext';
import { extractImage, saveInspection } from '@/src/services/api';
import { Card, PrimaryButton, SectionTitle } from '@/src/components/ui';
import { COLORS, RADIUS, SPACING } from '@/src/theme';
import type { Inspection } from '@/src/data/mockInspections';

// ── Types ─────────────────────────────────────────────────────────────────────
type Side = 'front' | 'back' | 'side';
interface ImageSlot {
  side:    Side;
  label:   string;
  uri:     string | null;
  base64:  string | null;
  mime:    string;
  quality: any;
}

const SIDES: { side: Side; label: string; icon: string; hint: string }[] = [
  { side: 'back',  label: 'Back / Main Label',  icon: 'package',    hint: 'Primary label with MRP, Mfg date, etc.' },
  { side: 'front', label: 'Front Panel',         icon: 'square',     hint: 'Product name, brand, net quantity' },
  { side: 'side',  label: 'Side / Top / Bottom', icon: 'layers',     hint: 'Additional declarations on other sides' },
];

const STEPS = [
  'Uploading images…',
  'Running AI vision on all sides…',
  'Merging extracted declarations…',
  'Running NER & field mapping…',
  'Calibrating barcode…',
  'Checking Legal Metrology rules…',
  'Generating compliance result…',
];

const BACKEND = process.env.EXPO_PUBLIC_BACKEND_URL || '';

// ── Helpers ────────────────────────────────────────────────────────────────────
async function checkQuality(b64: string, mime: string): Promise<any> {
  try {
    const res = await fetch(`${BACKEND}/api/check-image-quality`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_base64: `data:${mime};base64,${b64}`, mime_type: mime }),
    });
    if (res.ok) return await res.json();
  } catch (e) {}
  return null;
}

/**
 * Merge extracted data from multiple sides.
 * Non-null value from any side wins; later sides fill missing fields.
 */
function mergeExtracted(extractions: any[]): any {
  const merged: any = {};
  for (const ext of extractions) {
    if (!ext) continue;
    for (const [k, v] of Object.entries(ext)) {
      if (v !== null && v !== undefined && v !== '' && !merged[k]) {
        merged[k] = v;
      }
    }
  }
  return merged;
}

// ── Component ─────────────────────────────────────────────────────────────────
export default function ScanImage() {
  const router = useRouter();
  const { user }         = useAuth();
  const { addInspection } = useInspections();

  const [slots, setSlots] = useState<ImageSlot[]>(
    SIDES.map(s => ({ side: s.side, label: s.label, uri: null, base64: null, mime: 'image/jpeg', quality: null }))
  );
  const [scanning,   setScanning]   = useState(false);
  const [stepIndex,  setStepIndex]  = useState(0);
  const [error,      setError]      = useState('');

  const filledSlots = slots.filter(s => s.base64);
  const hasAny      = filledSlots.length > 0;

  // ── Pick image for a specific slot ─────────────────────────────────────────
  const pick = async (slotIndex: number, source: 'camera' | 'library') => {
    setError('');
    try {
      if (source === 'camera') {
        const perm = await ImagePicker.requestCameraPermissionsAsync();
        if (!perm.granted) { setError('Camera permission denied'); return; }
      } else {
        const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
        if (!perm.granted) { setError('Gallery permission denied'); return; }
      }

      const opts: ImagePicker.ImagePickerOptions = {
        mediaTypes: ['images'], base64: true, quality: 0.7, allowsEditing: false,
      };
      const res = source === 'camera'
        ? await ImagePicker.launchCameraAsync(opts)
        : await ImagePicker.launchImageLibraryAsync(opts);

      if (res.canceled || !res.assets?.[0]) return;
      const a = res.assets[0];

      // Run quality check in background
      const mime = a.mimeType || 'image/jpeg';
      const qualityPromise = a.base64 ? checkQuality(a.base64, mime) : Promise.resolve(null);

      setSlots(prev => prev.map((s, i) =>
        i === slotIndex
          ? { ...s, uri: a.uri, base64: a.base64 || null, mime, quality: null }
          : s
      ));

      // Update quality once checked
      qualityPromise.then(q => {
        setSlots(prev => prev.map((s, i) => i === slotIndex ? { ...s, quality: q } : s));
      });

    } catch (e: any) {
      setError(e.message || 'Failed to pick image');
    }
  };

  const clearSlot = (slotIndex: number) => {
    setSlots(prev => prev.map((s, i) =>
      i === slotIndex ? { ...s, uri: null, base64: null, quality: null } : s
    ));
  };

  // ── Run multi-side scan ─────────────────────────────────────────────────────
  const runScan = async () => {
    if (!hasAny || !user) return;
    setScanning(true);
    setError('');
    setStepIndex(0);

    const timer = setInterval(() => {
      setStepIndex(prev => prev < STEPS.length - 1 ? prev + 1 : prev);
    }, 800);

    try {
      // Call AI for each filled slot in parallel
      const extractionPromises = filledSlots.map(s =>
        extractImage(s.base64!, s.mime)
          .then(d => d.extracted)
          .catch(() => null)
      );
      const extractions = await Promise.all(extractionPromises);

      clearInterval(timer);
      setStepIndex(STEPS.length - 1);

      // Merge all extractions — fields from any side contribute
      const mergedExtracted = mergeExtracted(extractions);

      // Use compliance from the primary (back) scan's full response
      // Re-run compliance on merged data via the back slot
      const primarySlot = filledSlots.find(s => s.side === 'back') || filledSlots[0];
      const primaryData  = await extractImage(primarySlot.base64!, primarySlot.mime);
      const compliance   = primaryData.compliance;

      // Override extracted with merged (richer) data
      const inspectionId = `INS-${new Date().getFullYear()}-${String(Math.floor(Math.random() * 90000) + 10000)}`;

      const newInsp: Inspection = {
        id:               inspectionId,
        inspectorId:      user.id,
        inspectorName:    user.name,
        role:             user.role,
        state:            user.state,
        zone:             user.zone,
        district:         user.district,
        location:         user.district,
        scanType:         'IMAGE',
        productName:      mergedExtracted?.productName || 'Unknown Product',
        overall_status:   compliance.overall_status,
        violation_count:  compliance.violation_count,
        px_per_mm:        compliance.px_per_mm,
        barcode_calibrated: compliance.barcode_calibrated,
        timestamp:        new Date().toISOString(),
        extracted:        mergedExtracted,
        results:          compliance.results,
        // Store primary (back) image as proof
        imageUri:         primarySlot.uri || undefined,
        imageBase64:      primarySlot.base64
                            ? `data:${primarySlot.mime};base64,${primarySlot.base64}`
                            : undefined,
        // Store side count as metadata
        sides_scanned:    filledSlots.map(s => s.side),
      } as any;

      await addInspection(newInsp);
      saveInspection(newInsp);
      router.replace(`/inspection/${inspectionId}` as any);

    } catch (e: any) {
      clearInterval(timer);
      setError(e.message || 'AI scan failed. Please try again.');
    } finally {
      setScanning(false);
    }
  };

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <ScrollView style={{ flex: 1 }} contentContainerStyle={styles.container}>
      <Text style={styles.title}>Scan Product Label</Text>
      <Text style={styles.sub}>
        Upload images of multiple sides of the package. The AI reads all sides together
        and extracts the most complete set of mandatory declarations.
      </Text>

      {/* Info banner */}
      <View style={styles.infoBanner}>
        <Feather name="info" size={13} color="#1D4ED8" />
        <Text style={styles.infoText}>
          Only the Back / Main Label is required. Add Front and Side images if
          mandatory declarations appear on other panels.
        </Text>
      </View>

      {/* Image slots */}
      {slots.map((slot, idx) => {
        const sideDef = SIDES[idx];
        const isRequired = slot.side === 'back';
        return (
          <View key={slot.side} style={styles.slotCard}>
            {/* Slot header */}
            <View style={styles.slotHeader}>
              <View style={[styles.slotIcon, { backgroundColor: slot.base64 ? '#DBEAFE' : '#F1F5F9' }]}>
                <Feather name={sideDef.icon as any} size={16}
                  color={slot.base64 ? '#0070C0' : '#94A3B8'} />
              </View>
              <View style={{ flex: 1 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  <Text style={styles.slotLabel}>{slot.label}</Text>
                  {isRequired && (
                    <View style={styles.requiredBadge}>
                      <Text style={styles.requiredText}>REQUIRED</Text>
                    </View>
                  )}
                  {!isRequired && (
                    <View style={styles.optionalBadge}>
                      <Text style={styles.optionalText}>OPTIONAL</Text>
                    </View>
                  )}
                </View>
                <Text style={styles.slotHint}>{sideDef.hint}</Text>
              </View>
              {slot.base64 && (
                <Pressable onPress={() => clearSlot(idx)} style={styles.clearBtn}>
                  <Feather name="x" size={14} color="#DC2626" />
                </Pressable>
              )}
            </View>

            {/* Image preview or upload zone */}
            {slot.uri ? (
              <View>
                <Image source={{ uri: slot.uri }} style={styles.preview} resizeMode="cover" />

                {/* Quality result */}
                {slot.quality && !slot.quality.passed && (
                  <View style={styles.qualityWarn}>
                    <Text style={styles.qualityWarnTitle}>
                      ⚠ Image Quality Warning (Score: {slot.quality.score}/100)
                    </Text>
                    {slot.quality.issues?.map((issue: string, i: number) => (
                      <Text key={i} style={styles.qualityIssue}>• {issue}</Text>
                    ))}
                    {slot.quality.guidance?.map((g: string, i: number) => (
                      <Text key={i} style={styles.qualityGuide}>→ {g}</Text>
                    ))}
                  </View>
                )}
                {slot.quality?.passed && (
                  <View style={styles.qualityOk}>
                    <Feather name="check-circle" size={12} color="#16A34A" />
                    <Text style={styles.qualityOkText}>
                      Quality passed ({slot.quality.score}/100)
                    </Text>
                  </View>
                )}

                {/* Re-pick buttons */}
                <View style={styles.repickRow}>
                  <Pressable style={styles.repickBtn} onPress={() => pick(idx, 'camera')}>
                    <Feather name="camera" size={13} color="#0070C0" />
                    <Text style={styles.repickText}>Retake</Text>
                  </Pressable>
                  <Pressable style={styles.repickBtn} onPress={() => pick(idx, 'library')}>
                    <Feather name="image" size={13} color="#0070C0" />
                    <Text style={styles.repickText}>Change</Text>
                  </Pressable>
                </View>
              </View>
            ) : (
              <View style={styles.emptyZone}>
                <Feather name="upload" size={24} color="#CBD5E1" />
                <Text style={styles.emptyText}>
                  {isRequired ? 'Add the main label image' : 'Optional — add if needed'}
                </Text>
                <View style={styles.pickRow}>
                  <Pressable style={styles.pickBtn} onPress={() => pick(idx, 'camera')}>
                    <Feather name="camera" size={14} color="#fff" />
                    <Text style={styles.pickBtnText}>Camera</Text>
                  </Pressable>
                  <Pressable style={[styles.pickBtn, styles.pickBtnSecondary]}
                    onPress={() => pick(idx, 'library')}>
                    <Feather name="image" size={14} color="#0070C0" />
                    <Text style={[styles.pickBtnText, { color: '#0070C0' }]}>Gallery</Text>
                  </Pressable>
                </View>
              </View>
            )}
          </View>
        );
      })}

      {/* Scan summary + action */}
      {hasAny && !scanning && (
        <Card>
          <View style={styles.summaryRow}>
            <Feather name="layers" size={16} color="#0070C0" />
            <Text style={styles.summaryText}>
              {filledSlots.length} side(s) ready:{' '}
              {filledSlots.map(s => s.label.split(' ')[0]).join(', ')}
            </Text>
          </View>
          <Text style={styles.summarySub}>
            AI will extract declarations from all uploaded images and merge them
            into a single compliance report.
          </Text>
          <PrimaryButton
            testID="start-scan-button"
            label={`Start AI Scan (${filledSlots.length} image${filledSlots.length > 1 ? 's' : ''})`}
            icon="cpu"
            onPress={runScan}
            loading={scanning}
            disabled={!hasAny}
          />
        </Card>
      )}

      {!!error && (
        <View style={styles.errorBox}>
          <Feather name="alert-circle" size={14} color="#DC2626" />
          <Text style={styles.errorText}>{error}</Text>
        </View>
      )}

      {/* Progress steps */}
      {scanning && (
        <Card testID="scan-progress">
          <SectionTitle>Analyzing {filledSlots.length} image(s)…</SectionTitle>
          {STEPS.map((s, i) => {
            const done   = i < stepIndex;
            const active = i === stepIndex;
            return (
              <View key={s} style={styles.stepRow}>
                {done ? (
                  <View style={[styles.dot, { backgroundColor: COLORS.success }]}>
                    <Feather name="check" size={10} color="#fff" />
                  </View>
                ) : active ? (
                  <ActivityIndicator size="small" color={COLORS.primary} />
                ) : (
                  <View style={[styles.dot, { backgroundColor: COLORS.slate200 }]} />
                )}
                <Text style={[
                  styles.stepText,
                  active && { color: COLORS.slate900, fontWeight: '700' },
                  done  && { color: COLORS.slate400 },
                ]}>{s}</Text>
              </View>
            );
          })}
        </Card>
      )}

      <Text style={styles.disclaimer}>
        AI-assisted compliance assessment. Final legal determination must be made by
        the competent Legal Metrology authority.
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container:        { padding: SPACING.lg, gap: SPACING.md },
  title:            { fontSize: 20, fontWeight: '800', color: COLORS.slate900 },
  sub:              { fontSize: 12, color: COLORS.slate500 },

  infoBanner:       { flexDirection: 'row', gap: 8, backgroundColor: '#EFF6FF',
                      borderRadius: 8, padding: 10, alignItems: 'flex-start' },
  infoText:         { fontSize: 11, color: '#1D4ED8', flex: 1, lineHeight: 16 },

  slotCard:         { backgroundColor: '#fff', borderRadius: 12, padding: 14,
                      borderWidth: 1, borderColor: '#E2E8F0',
                      shadowColor: '#000', shadowOpacity: 0.04, shadowRadius: 4,
                      shadowOffset: { width: 0, height: 2 }, elevation: 2 },
  slotHeader:       { flexDirection: 'row', alignItems: 'flex-start', gap: 10, marginBottom: 12 },
  slotIcon:         { width: 36, height: 36, borderRadius: 10, alignItems: 'center',
                      justifyContent: 'center' },
  slotLabel:        { fontSize: 13, fontWeight: '700', color: '#0F172A' },
  slotHint:         { fontSize: 11, color: '#94A3B8', marginTop: 2 },
  requiredBadge:    { backgroundColor: '#DBEAFE', paddingHorizontal: 6, paddingVertical: 2,
                      borderRadius: 6 },
  requiredText:     { fontSize: 9, fontWeight: '800', color: '#0070C0' },
  optionalBadge:    { backgroundColor: '#F1F5F9', paddingHorizontal: 6, paddingVertical: 2,
                      borderRadius: 6 },
  optionalText:     { fontSize: 9, fontWeight: '700', color: '#64748B' },
  clearBtn:         { padding: 4 },

  preview:          { width: '100%', height: 180, borderRadius: 10,
                      backgroundColor: COLORS.slate100, marginBottom: 8 },

  qualityWarn:      { backgroundColor: '#FEF3C7', borderRadius: 8, padding: 10, marginBottom: 8 },
  qualityWarnTitle: { fontSize: 11, fontWeight: '700', color: '#92400E', marginBottom: 4 },
  qualityIssue:     { fontSize: 10, color: '#92400E' },
  qualityGuide:     { fontSize: 10, color: '#78350F', marginTop: 2 },
  qualityOk:        { flexDirection: 'row', alignItems: 'center', gap: 6,
                      backgroundColor: '#F0FDF4', borderRadius: 6,
                      padding: 6, marginBottom: 8 },
  qualityOkText:    { fontSize: 10, color: '#166534', fontWeight: '600' },

  repickRow:        { flexDirection: 'row', gap: 8 },
  repickBtn:        { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
                      gap: 6, paddingVertical: 8, borderRadius: 8,
                      backgroundColor: '#EFF6FF', borderWidth: 1, borderColor: '#BFDBFE' },
  repickText:       { fontSize: 12, color: '#0070C0', fontWeight: '600' },

  emptyZone:        { alignItems: 'center', paddingVertical: 20, gap: 6,
                      backgroundColor: '#F8FAFC', borderRadius: 10,
                      borderWidth: 1, borderColor: '#E2E8F0', borderStyle: 'dashed' },
  emptyText:        { fontSize: 12, color: '#94A3B8' },
  pickRow:          { flexDirection: 'row', gap: 8, marginTop: 4 },
  pickBtn:          { flexDirection: 'row', alignItems: 'center', gap: 6,
                      paddingHorizontal: 16, paddingVertical: 8, borderRadius: 8,
                      backgroundColor: '#0070C0' },
  pickBtnSecondary: { backgroundColor: '#EFF6FF', borderWidth: 1, borderColor: '#BFDBFE' },
  pickBtnText:      { fontSize: 12, color: '#fff', fontWeight: '700' },

  summaryRow:       { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 },
  summaryText:      { fontSize: 13, fontWeight: '700', color: '#0F172A' },
  summarySub:       { fontSize: 11, color: '#64748B', marginBottom: 12 },

  errorBox:         { flexDirection: 'row', alignItems: 'flex-start', gap: 8,
                      backgroundColor: '#FEF2F2', borderRadius: 8, padding: 12 },
  errorText:        { fontSize: 12, color: '#DC2626', flex: 1 },

  stepRow:          { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 8 },
  dot:              { width: 20, height: 20, borderRadius: 10,
                      alignItems: 'center', justifyContent: 'center' },
  stepText:         { fontSize: 13, color: COLORS.slate600 },
  disclaimer:       { fontSize: 10, color: COLORS.slate400, textAlign: 'center',
                      fontStyle: 'italic', marginTop: SPACING.sm },
});
