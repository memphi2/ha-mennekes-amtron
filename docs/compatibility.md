# Device compatibility

The integration implements the manufacturer's **Modbus RTU over RS-485**
interface for Amtron 4You 300, Compact 2.0s and Start 2.0s. The common
[RTU specification, revision 2.5](https://www.mennekes.de/fileadmin/MEN-Deutschland/emobility/01_documents/04_installer/Modbus_RTU_Specification_AMTRON_4You300_Compact_2.0s_Start_2.0s-v2.5.pdf)
names these three families. Model recognition is independent of hardware testing.

## Model matrix

All variants below are covered by that interface specification. **Reported**
means the owner reported successful operation with the released integration.
**Unverified** means no physical-device test has been reported for that variant.

| Model | Recognized article numbers | Hardware test |
|---|---|---|
| Amtron 4You 310 7.4 C2 | `1313207205`, `1313207205BK` | Unverified |
| Amtron 4You 310 11 C2 | `1313201205`, `1313201205BK` | Reported, 2026-11-03 |
| Amtron 4You 310 11 T2S | `1313201605`, `1313201605BK` | Unverified |
| Amtron 4You 310 22 C2 | `1313202205`, `1313202205BK` | Unverified |
| Amtron 4You 310 11 C2 PV-Bundle | `1313201265`, `1313201265BK` | Unverified |
| Amtron Compact 2.0s 7.4 C2 | `1321307205` | Unverified |
| Amtron Compact 2.0s 11 C2 | `1321301205` | Unverified |
| Amtron Compact 2.0s 22 C2 | `1321302205` | Unverified |
| Amtron Compact 2.0s 11 C2 PV-Bundle | `1321301265` | Unverified |
| Amtron Start 2.0s 7.4 | `1331307405` | Unverified |
| Amtron Start 2.0s 7.4 T2S | `1331307605` | Unverified |
| Amtron Start 2.0s 22 | `1331302405` | Unverified |
| Amtron Start 2.0s 22 T2S | `1331302605` | Unverified |

The hardware report covers the released integration on an Amtron 4You 310
11 C2. The owner reported that the functions they used worked. Firmware,
individual test cases and a long-term Recorder measurement were not supplied.
The report does not validate subsequent development changes or other models.

The socket variants are included too: **4You 310 11 T2S** and **Start 2.0s
7.4/22 T2S**. MENNEKES explicitly added the 4You 310 11 T2S in the firmware
2.2_11282 notes. The Start 2.0s 7.4/22 variants without a suffix retain the
names used in the manufacturer's declaration. A T2/T2S suffix alone does not
establish RTU compatibility for another Amtron family.

## Model identification

Register `0x001B` provides the article number from layout v01.03 onward;
it does not supply a human-readable model name. The integration matches
complete known numbers, including the listed `BK` variants. MENNEKES documents
the numeric article `1313201205` in its RTU specification and the `BK` ordering
variant in its catalog. The other listed 4You numeric forms follow that same
base-number convention; their exact register output is not hardware-verified.
PV bundles may report the article of the included wallbox instead of the set.

The model is shown separately from the article number. Leading underscore
padding is removed from displayed serial/article numbers. Existing serial-based
registry identities remain unchanged. Unknown or unavailable article numbers
use **Amtron** as the model; an unknown number remains visible as the model ID.
Unknown numbers do not prevent setup on a supported register layout.

Article/name sources from MENNEKES:

- [4You portfolio, including the 7.4, 11 and 22 kW variants](https://www.mennekes.ch/emobility/produkte/portfolio/amtronr-wallboxen/)
- [4You 310 11 C2 product sheet](https://www.mennekes.de/fileadmin/products_media/medien_sge2/produktdatenblaetter/20241218/1313201205BK_de.pdf)
- [4You firmware notes identifying the 310 11 T2S socket variant](https://www.mennekes.de/fileadmin/MEN-Deutschland/emobility/04_software/12_amtron_4you_100_300/Release_Notes_for_AMTRON_4You_110_310_SW_2.22.pdf)
- [4You 310 11 C2 PV-Bundle](https://www.mennekes.de/emobility/produkt-details/1313201265BK/)
- [Compact 2.0s and Start 2.0s declaration listing all seven variants](https://www.mennekes.de/fileadmin/products_media/medien_sge2/konformitaetserklaerungen/1321301205_ke.pdf)
- [Compact 2.0s 11 C2 PV-Bundle](https://www.mennekes.de/emobility/produkt-details/1321301265/)

## Functional limits

Entities depend on the reported register layout and capabilities, not the
model label. Layouts v01.00–v01.02 expose fewer registers; v01.03 is the
implemented reference. Dynamic phase switching requires the device to report
support; it is not enabled solely because a model has an 11 kW rating.

Other Amtron families, including 4You 100/400/500, Compact 2.0 without the
**s**, and Modbus TCP products, are outside this documented RTU compatibility
scope. Similar names or access through the same configuration tool do not
establish compatibility. Use one entry per wallbox and one master per serial bus.

A [LAN-to-RS-485 adapter](network-gateway.md) only changes how this integration
reaches those supported RTU models. Transparent RTU over TCP and translating
Modbus TCP-to-RTU gateways do not add compatibility with other native Modbus TCP
wallbox families or their different register maps.
