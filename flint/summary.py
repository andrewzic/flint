"""Summary containers intended to hold general information obtained throughout a processing run. 
"""
from __future__ import (  # Used for mypy/pylance to like the return type of MS.with_options
    annotations,
)

from pathlib import Path
from typing import NamedTuple, Optional, Union, Tuple, Collection

import astropy.units as u
from astropy.coordinates import AltAz, EarthLocation, Latitude, Longitude, SkyCoord
from astropy.table import Table
from astropy.time import Time

from flint.logging import logger
from flint.ms import (
    MS,
    MSSummary,
    describe_ms,
    get_telescope_location_from_ms,
    get_times_from_ms,
)
from flint.naming import get_sbid_from_path, processed_ms_format
from flint.source_finding.aegean import AegeanOutputs
from flint.utils import estimate_image_centre


class FieldSummary(NamedTuple):
    """The main information about a processed field. This structure is
    intended to store critical components that might be accumulated throughout
    processing of a pipeline, and may be most useful when data-products
    are zipped (or removed) throughout. Its intended usage is to hold key
    components that might be used in stages like validation plotting. It is not
    intended to become a catch-all to replace passing through items into
    functions directly.

    There are known issues around serialising astropy units within a dask/prefect environment,
    for example: https://github.com/astropy/astropy/issues/11317,
    This could become important if an instance of this object is exchaned
    between many prefect or dask like delayed tasks. Ye be warned.
    """

    sbid: str
    """SBID of processed field"""
    cal_sbid: str
    """SBID of the bandpass calibrator"""
    field_name: str
    """The name of the field"""
    ms_summaries: Optional[Tuple[MSSummary]] = None
    """Summaries of measurement sets used in the processing of the filed"""
    centre: Optional[SkyCoord] = None
    """Centre of the field, which is calculated from the centre of an image"""
    integration_time: Optional[int] = None
    """The integration time of the observation (seconds)"""
    no_components: Optional[int] = None
    """Number of components found from the source finder"""
    holography_path: Optional[Path] = None
    """Path to the file used for holography"""
    round: Optional[int] = None
    """The self-cal round"""
    location: Optional[EarthLocation] = None
    """The location of the telescope stored as (X,Y,Z) in meters"""
    ms_times: Optional[Time] = None
    """The unique scan times of integrations stored in the measurement set"""
    hour_angles: Optional[Longitude] = None
    """Computed hour-angles of the field"""
    elevations: Optional[Latitude] = None
    """Computed elevations of the field"""
    median_rms: Optional[float] = None
    """The meanian RMS computed from an RMS image"""

    def with_options(self, **kwargs) -> FieldSummary:
        prop = self._asdict()
        prop.update(**kwargs)

        return FieldSummary(**prop)


# TODO: Need to establise a MSLike type
def add_ms_summaries(
    field_summary: FieldSummary, mss: Collection[MS]
) -> Tuple[MSSummary]:
    """Obtain a MSSummary instance to add to a FieldSummary

    Args:
        field_summary (FieldSummary): Existing field summary object to update
        mss (Collection[MS]): Set of measurement sets to describe

    Returns:
        Tuple[MSSummary]: Results from the inspected set of measurement sets
    """

    ms_summaries = tuple(map(describe_ms, mss))

    field_summary = field_summary.with_options(ms_summaries=ms_summaries)

    return field_summary


def add_rms_information(
    field_summary: FieldSummary, aegean_outputs: AegeanOutputs
) -> FieldSummary:
    """Add information derived from an RMS image and component catalogue
    to an existing `FieldSummary` instance. Some properteries, such as
    the center position, number of components etc, are taken directly
    from source finder products.

    On the center position -- there is not (at the moment) a simple
    way of getting the center position of a field. So the image
    itself is used to grab it.

    Other properties that require components of a measurement set,
    including the time and position, are also derived using existing
    fields which are created when a new instance is made.

    Args:
        field_summary (FieldSummary): Existing field summary object to update
        aegean_outputs (AegeanOutputs): Products of a source finding run

    Returns:
        FieldSummary: Updated field summary object
    """
    # NOTE: The only reason this pirate is using the RMS image here is
    # because at the moment there is not a neat way to get the (rough)
    # field center. Access in a meaningful way to the observatory database
    # would be useful (like the metafits service for the MWA). Since
    # the principal use of a FieldSummary is in the validation plot/table
    # and the RMS / component catalogue is expected, this pirate can
    # handle it. With some ale. It is not as simple as reading the position
    # of the 18th beam as the beam numbers and their order differ depending
    # on the footprint layout.
    # TODO: Consider trying to take the mean position from the phase direction
    # from all science MSs

    rms_image_path = aegean_outputs.rms

    centre = estimate_image_centre(image_path=rms_image_path).icrs

    telescope = field_summary.location
    ms_times = field_summary.ms_times
    centre_altaz = centre.transform_to(AltAz(obstime=ms_times, location=telescope))
    hour_angles = centre_altaz.az.to(u.hourangle)
    elevations = centre_altaz.alt.to(u.deg)

    no_components = len(Table.read(aegean_outputs.comp))

    field_summary = field_summary.with_options(
        centre=centre,
        hour_angles=hour_angles,
        elevations=elevations,
        no_components=no_components,
    )

    return field_summary


def update_field_summary(
    field_summary: FieldSummary,
    aegean_outputs: Optional[AegeanOutputs] = None,
    mss: Optional[Collection[MS]] = None,
    **kwargs,
) -> FieldSummary:
    """Update an existing `FieldSummary` instance with additional information.

    If special steps are required to be carried out based on a known input they will be.
    Otherwise all additional keyword arguments are passed through to the `FieldSummary.with_options`.

    Args:
        field_summary (FieldSummary): Field summary object to update
        aegean_outputs (Optional[AegeanOutputs], optional): Will add RMS and aegean related properties. Defaults to None.
        mss (Optional[Collection[MS]], optionals): Set of measurement sets to describe

    Returns:
        FieldSummary: An updated field summary objects
    """

    if aegean_outputs:
        field_summary = add_rms_information(
            field_summary=field_summary, aegean_outputs=aegean_outputs
        )

    if mss:
        field_summary = add_ms_summaries(field_summary=field_summary, mss=mss)

    field_summary = field_summary.with_options(**kwargs)

    logger.info(f"Updated {field_summary=}")
    return field_summary


def create_field_summary(
    ms: Union[MS, Path],
    cal_sbid_path: Optional[Path] = None,
    holography_path: Optional[Path] = None,
    aegean_outputs: Optional[AegeanOutputs] = None,
    mss: Optional[Collection[MS]] = None,
) -> FieldSummary:
    """Create a field summary object using a measurement set.

    Args:
        ms (Union[MS, Path]): Measurement set information will be pulled from
        cal_sbid_path (Optional[Path], optional): Path to an example of a bandpass measurement set. Defaults to None.
        holography_path (Optional[Path], optional): The holography fits cube used (or will be) to linmos. Defaults to None.
        aegean_outputs (Optional[AegeanOutputs], optional): Should RMS / source information be added to the instance. Defaults to None.
        mss (Optional[Collection[MS]], optionals): Set of measurement sets to describe

    Returns:
        FieldSummary: A summary of a field
    """
    # TODO: Maybe this should be changed to accept all MSs as
    # the input argument in place of a singular ms. In otherwords
    # all measdurement sets that have gone into the field. Need ale.
    # Will talk to the parrot.

    logger.info("Creating field summary object")

    ms = MS.cast(ms=ms)

    ms_components = processed_ms_format(in_name=ms.path)

    sbid = str(ms_components.sbid)
    field = ms_components.field

    assert field is not None, f"Field name is empty in {ms_components=} from {ms.path=}"

    cal_sbid = str(get_sbid_from_path(path=cal_sbid_path)) if cal_sbid_path else None

    ms_times = get_times_from_ms(ms=ms)
    integration = ms_times.ptp().to(u.second).value
    location = get_telescope_location_from_ms(ms=ms)

    field_summary = FieldSummary(
        sbid=sbid,
        field_name=field,
        cal_sbid=cal_sbid,
        location=location,
        integration_time=integration,
        holography_path=holography_path,
        ms_times=Time([ms_times.min(), ms_times.max()]),
    )

    if aegean_outputs:
        field_summary = add_rms_information(
            field_summary=field_summary, aegean_outputs=aegean_outputs
        )

    if mss:
        field_summary = add_ms_summaries(field_summary=field_summary, mss=mss)

    return field_summary
