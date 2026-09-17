import React, { useState } from 'react';
import { View, Text, TextInput, StyleSheet, KeyboardAvoidingView, Platform, ScrollView, Pressable } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Feather } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useAuth } from '@/src/context/AuthContext';
import { COLORS, RADIUS, SPACING } from '@/src/theme';
import { PrimaryButton } from '@/src/components/ui';
import { DEMO_USERS } from '@/src/data/demoUsers';

export default function Login() {
  const { login } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState('inspector@labeldoc.gov.in');
  const [password, setPassword] = useState('ins123');
  const [showPass, setShowPass] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleLogin = async () => {
    setError('');
    setLoading(true);
    const res = await login(email, password);
    setLoading(false);
    if (!res.ok) setError(res.error || 'Login failed');
    else router.replace('/dashboard');
  };

  const quickFill = (u: typeof DEMO_USERS[number]) => {
    setEmail(u.email);
    setPassword(u.password);
  };

  return (
    <SafeAreaView style={styles.safe} edges={['top', 'bottom']}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={styles.container} keyboardShouldPersistTaps="handled">
          <View style={styles.brand}>
            <View style={styles.logoBox}>
              <Feather name="shield" size={32} color={COLORS.brand} />
            </View>
            <Text style={styles.title}>LABELDOC</Text>
            <Text style={styles.subtitle}>AI-Assisted Legal Metrology{'\n'}Compliance Inspection Platform</Text>
          </View>

          <View style={styles.card}>
            <Text style={styles.label}>Email</Text>
            <View style={styles.inputRow}>
              <Feather name="mail" size={16} color={COLORS.slate400} />
              <TextInput
                testID="login-email-input"
                value={email}
                onChangeText={setEmail}
                placeholder="officer@labeldoc.gov.in"
                placeholderTextColor={COLORS.slate400}
                autoCapitalize="none"
                keyboardType="email-address"
                style={styles.input}
              />
            </View>

            <Text style={[styles.label, { marginTop: SPACING.md }]}>Password</Text>
            <View style={styles.inputRow}>
              <Feather name="lock" size={16} color={COLORS.slate400} />
              <TextInput
                testID="login-password-input"
                value={password}
                onChangeText={setPassword}
                placeholder="••••••••"
                placeholderTextColor={COLORS.slate400}
                secureTextEntry={!showPass}
                style={styles.input}
              />
              <Pressable testID="login-toggle-password" onPress={() => setShowPass(s => !s)}>
                <Feather name={showPass ? 'eye-off' : 'eye'} size={16} color={COLORS.slate500} />
              </Pressable>
            </View>

            {!!error && <Text testID="login-error" style={styles.error}>{error}</Text>}

            <View style={{ height: SPACING.lg }} />
            <PrimaryButton testID="login-submit-button" label="Sign In" onPress={handleLogin} loading={loading} icon="arrow-right" />
          </View>

          <View style={styles.demoCard}>
            <Text style={styles.demoTitle}>Prototype demo accounts</Text>
            {DEMO_USERS.map(u => (
              <Pressable key={u.id} testID={`demo-user-${u.role}`} onPress={() => quickFill(u)} style={styles.demoRow}>
                <View style={{ flex: 1 }}>
                  <Text style={styles.demoName}>{u.name}</Text>
                  <Text style={styles.demoRole}>{u.role.replace(/_/g, ' ')} · {u.district}</Text>
                </View>
                <Feather name="chevron-right" size={16} color={COLORS.slate400} />
              </Pressable>
            ))}
            <Text style={styles.protoNote}>Prototype System — for demonstration only.</Text>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: COLORS.surface },
  container: { padding: SPACING.lg, gap: SPACING.lg },
  brand: { alignItems: 'center', marginTop: SPACING.xl, gap: SPACING.sm },
  logoBox: {
    width: 64, height: 64, borderRadius: RADIUS.md, backgroundColor: COLORS.brandLight,
    alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: COLORS.brand + '33',
  },
  title: { fontSize: 24, fontWeight: '900', color: COLORS.slate900, letterSpacing: 2 },
  subtitle: { fontSize: 12, color: COLORS.slate500, textAlign: 'center', lineHeight: 18 },
  card: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.md, padding: SPACING.lg,
    borderWidth: 1, borderColor: COLORS.border,
  },
  label: { fontSize: 12, color: COLORS.slate600, fontWeight: '700', marginBottom: 6 },
  inputRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    borderWidth: 1, borderColor: COLORS.borderStrong, borderRadius: RADIUS.sm,
    paddingHorizontal: 12, paddingVertical: Platform.OS === 'ios' ? 12 : 4,
    backgroundColor: COLORS.surface,
  },
  input: { flex: 1, fontSize: 14, color: COLORS.slate900, paddingVertical: 8 },
  error: { color: COLORS.error, fontSize: 12, marginTop: 10, fontWeight: '600' },
  demoCard: {
    backgroundColor: COLORS.card, borderRadius: RADIUS.md, padding: SPACING.md,
    borderWidth: 1, borderColor: COLORS.border,
  },
  demoTitle: { fontSize: 12, fontWeight: '700', color: COLORS.slate600, marginBottom: SPACING.sm, textTransform: 'uppercase', letterSpacing: 0.5 },
  demoRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 10, borderTopWidth: 1, borderTopColor: COLORS.divider, gap: 8 },
  demoName: { fontSize: 13, fontWeight: '600', color: COLORS.slate900 },
  demoRole: { fontSize: 11, color: COLORS.slate500, marginTop: 2 },
  protoNote: { fontSize: 10, color: COLORS.slate400, textAlign: 'center', marginTop: SPACING.sm, fontStyle: 'italic' },
});
